from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.agent_harness import HarnessStore, apply_actions, build_context, create_run, record_tool_result, run_invocation
from tools.lib.canonical import canonical_sha256
from tools.lib.diagnostics import DiagnosticError
from tools.lib.runtime import Runtime
from tools.lib.yaml_io import load_json, load_yaml
from tools.new_production import main as new_production_main
from tools.run_agent_harness import main as run_agent_harness_main
from tools.validate import validate_project


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/task-matrix"
STARTED = "2026-08-27T12:00:00+09:00"


class AgentHarnessTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        output = root / "output"
        self.assertEqual(new_production_main(["harness-test", "--handoff", str(FIXTURE), "--output-root", str(output)]), 0)
        project = output / "production/harness-test"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        plan_path = project / "03_plan/production-plan.yaml"
        plan = load_yaml(plan_path)
        for resource in plan.get("resources", []):
            resource["availability"] = "AVAILABLE"
        for material in plan.get("materials", []):
            material["status"] = "APPROVED"
        plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}
        from tools.lib.yaml_io import dump_yaml

        dump_yaml(plan, plan_path)
        return project

    def _run(self, project: Path) -> tuple[HarnessStore, dict, dict, dict, str]:
        runtime = Runtime(project, ROOT)
        runtime.bootstrap(occurred_at=STARTED, actor_kind="SYSTEM", actor_id="test/runtime")
        runtime.initialize_task_graph(occurred_at=STARTED, actor_kind="SYSTEM", actor_id="test/runtime")
        task_id = runtime.next_task(occurred_at=STARTED)
        self.assertEqual(task_id, "TK004")
        actor_id = "agent/ARN000001"
        lease_token = "harness/ARN000001/TK004/1"
        runtime.claim_task(task_id=task_id, occurred_at=STARTED, actor_id=actor_id, lease_token=lease_token, expires_at="2026-08-27T13:00:00+09:00", idempotency_key="test/harness/claim")
        context, grant = build_context(project, ROOT, task_id=task_id, lease_token=lease_token, run_id="ARN000001", generated_at=STARTED, actor_kind="AGENT", actor_id=actor_id)
        run = create_run(project, ROOT, run_id="ARN000001", adapter_profile_id="scripted-fake", target_state="COMPLETE", started_at=STARTED, actor_id=actor_id, task_ref={"id": task_id, "revision": 1, "sha256": context["task"]["sha256"]}, lease_token=lease_token, context=context, grant=grant)
        return HarnessStore(project, ROOT), context, grant, run, lease_token

    def test_context_worker_broker_and_replay_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, context, grant, run, lease_token = self._run(self._project(Path(directory)))
            project = store.project
            context_path = store.context_path(context["integrity"]["content_sha256"])
            self.assertNotIn(str(project), context_path.read_text(encoding="utf-8"))
            self.assertNotIn('"lease_token":', context_path.read_text(encoding="utf-8"))
            invocation, actions = run_invocation(project, ROOT, run_id="ARN000001", task_id="TK004", lease_token=lease_token, context_sha256=context["integrity"]["content_sha256"], adapter_profile_id="scripted-fake", invocation_id="AIN000001", started_at="2026-08-27T12:00:01+09:00")
            self.assertEqual(invocation["status"], "SUCCEEDED")
            tool_action = json.loads(json.dumps(actions[0]))
            tool_action.update({"action_id": "AAC000002", "kind": "REQUEST_TOOL", "idempotency_key": "AIN000001/action/tool"})
            tool_action["payload"] = {"tool_id": "read_task", "input": {}, "target_ref": "urn:test:task:TK004", "target_sha256": context["task"]["sha256"]}
            applied = apply_actions(project, ROOT, run_id="ARN000001", invocation_id="AIN000001", lease_token=lease_token, actions=actions + [tool_action], applied_at="2026-08-27T12:00:02+09:00", actor_id=run["actor"]["id"])
            self.assertEqual(applied["status"], "APPLIED")
            record_tool_result(project, ROOT, run_id="ARN000001", tool_request_id="ATR000002", status="SUCCEEDED", result_sha256=context["task"]["sha256"], input_bytes=2, output_bytes=2, recorded_at="2026-08-27T12:00:04+09:00", actor_id="tool/read_task")
            self.assertEqual(store.replay("ARN000001")["action_count"], 2)
            self.assertEqual(validate_project(project, ROOT), [])

    def test_stale_or_direct_transition_proposal_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, context, grant, run, lease_token = self._run(self._project(Path(directory)))
            project = store.project
            before = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            invocation, actions = run_invocation(project, ROOT, run_id="ARN000001", task_id="TK004", lease_token=lease_token, context_sha256=context["integrity"]["content_sha256"], adapter_profile_id="scripted-fake", invocation_id="AIN000001", started_at="2026-08-27T12:00:01+09:00")
            malicious = json.loads(json.dumps(actions[0]))
            malicious["action_id"] = "AAC000002"
            malicious["payload"]["to_state"] = "COMPLETE"
            with self.assertRaises(DiagnosticError) as raised:
                apply_actions(project, ROOT, run_id="ARN000001", invocation_id=invocation["invocation_id"], lease_token=lease_token, actions=[malicious], applied_at="2026-08-27T12:00:02+09:00", actor_id=run["actor"]["id"])
            self.assertEqual(raised.exception.finding.rule, "AGENT_ACTION_SCHEMA")
            after = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            self.assertEqual(before.keys() | {"08_runtime/agent-harness/invocations/AIN000001.json", "08_runtime/agent-harness/responses/AIN000001.json"}, after.keys())
            self.assertEqual(validate_project(project, ROOT), [])

    def test_harness_projection_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, _, _, _, _ = self._run(self._project(Path(directory)))
            state_path = store.path("agent-run-state.json")
            state = load_json(state_path)
            state["step"] = 99
            state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            findings = validate_project(store.project, ROOT)
            self.assertTrue(any(finding.rule == "AGENT_STATE_DIVERGENCE" for finding in findings))

    def test_orchestrator_cli_start_run_and_status_is_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(Path(directory))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(run_agent_harness_main(["start", "--project-root", str(project), "--run-id", "ARN000001", "--started-at", STARTED]), 0)
                self.assertEqual(run_agent_harness_main(["run", "--project-root", str(project), "--run-id", "ARN000001", "--occurred-at", "2026-08-27T12:00:01+09:00"]), 0)
                self.assertEqual(run_agent_harness_main(["status", "--project-root", str(project), "--run-id", "ARN000001"]), 0)
            self.assertIn('"status": "COMPLETE"', output.getvalue())
            self.assertEqual(validate_project(project, ROOT), [])


if __name__ == "__main__":
    unittest.main()
