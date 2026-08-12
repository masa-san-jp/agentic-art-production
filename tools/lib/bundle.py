"""Offline handoff bundle loading and integrity checks."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .canonical import canonical_sha256, handoff_sha256, sha256_bytes
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .schema import load_schema, validate_instance
from .security import check_text_security, safe_relative_path
from .yaml_io import load_yaml, read_text


IGNORED_FILESYSTEM_METADATA = {"Icon\r"}


def _error(rule: str, reason: str, *, file: str = "", location: str | None = None, remediation: str) -> DiagnosticError:
    return DiagnosticError(Finding(rule, reason, file=file, location=location, remediation=remediation))


@dataclass
class Bundle:
    root: Path
    temporary_root: Path | None
    manifest: dict[str, Any]
    handoff: dict[str, Any]
    schema: dict[str, Any]
    provenance: dict[str, Any]
    declared_files: tuple[str, ...]

    def close(self) -> None:
        if self.temporary_root is not None:
            shutil.rmtree(self.temporary_root, ignore_errors=True)
            self.temporary_root = None

    def copy_to(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        for relative in ("manifest.yaml", *self.declared_files):
            source = self.root / Path(relative)
            target = destination / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


@contextmanager
def open_bundle(path: Path, repository_root: Path) -> Iterator[Bundle]:
    bundle = _load_bundle(path, repository_root)
    try:
        yield bundle
    finally:
        bundle.close()


def _load_bundle(path: Path, repository_root: Path) -> Bundle:
    safety = load_config(repository_root, "safety-policy.yaml")
    temporary_root: Path | None = None
    if path.is_symlink():
        raise _error("BUNDLE_SYMLINK", "bundle root must not be a symlink", file=str(path), remediation="Provide a real directory or ZIP file.")
    if path.is_dir():
        root = path
    elif path.is_file() and path.suffix.lower() == ".zip":
        max_zip = int(safety.get("max_zip_bytes", 4 * 1024 * 1024))
        if path.stat().st_size > max_zip:
            raise _error("ARCHIVE_TOO_LARGE", "ZIP archive exceeds the configured compressed size limit", file=str(path), remediation="Export a smaller text-only bundle.")
        temporary_root = Path(tempfile.mkdtemp(prefix="agentic-art-production-bundle-"))
        root = temporary_root / "root"
        root.mkdir()
        _extract_zip(path, root, safety)
    else:
        raise _error("BUNDLE_TYPE", "handoff input must be a directory or ZIP archive", file=str(path), remediation="Provide an exported handoff directory or unencrypted ZIP archive.")

    try:
        return _validate_bundle(root, temporary_root, repository_root, safety)
    except Exception:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def _extract_zip(archive_path: Path, target: Path, safety: dict[str, Any]) -> None:
    max_total = int(safety.get("max_bundle_bytes", 8 * 1024 * 1024))
    max_file = int(safety.get("max_bundle_file_bytes", 1024 * 1024))
    max_files = int(safety.get("max_bundle_files", 64))
    max_ratio = float(safety.get("max_compression_ratio", 20))
    total = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > max_files + 1:
                raise _error("ARCHIVE_FILE_COUNT", "ZIP archive contains too many entries", file=str(archive_path), remediation="Reduce the bundle to the declared text files.")
            for info in infos:
                relative = safe_relative_path(info.filename, max_bytes=int(safety.get("max_path_bytes", 240)), max_depth=int(safety.get("max_path_depth", 8)))
                if relative in seen:
                    raise _error("ARCHIVE_DUPLICATE_ENTRY", f"ZIP archive repeats {relative!r}", file=str(archive_path), remediation="Remove duplicate archive entries.")
                seen.add(relative)
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise _error("ARCHIVE_ENCRYPTED", "encrypted ZIP entries are not allowed", file=str(archive_path), location=relative, remediation="Export an unencrypted ZIP bundle.")
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in {0, 0o100000}:
                    raise _error("ARCHIVE_SPECIAL_FILE", "ZIP entry is not a regular file", file=str(archive_path), location=relative, remediation="Remove symlinks and special files from the bundle.")
                if info.file_size > max_file:
                    raise _error("BUNDLE_FILE_TOO_LARGE", f"file exceeds {max_file} bytes", file=str(archive_path), location=relative, remediation="Keep bundle files below the configured limit.")
                total += info.file_size
                if total > max_total:
                    raise _error("BUNDLE_TOO_LARGE", "uncompressed ZIP content exceeds the configured limit", file=str(archive_path), remediation="Export a smaller text-only bundle.")
                if info.compress_size and info.file_size / info.compress_size > max_ratio:
                    raise _error("ARCHIVE_COMPRESSION_RATIO", "ZIP entry has an unsafe compression ratio", file=str(archive_path), location=relative, remediation="Use a normal-compression text bundle.")
                output = target / Path(relative)
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, output.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=64 * 1024)
    except zipfile.BadZipFile as exc:
        raise _error("ARCHIVE_INVALID", str(exc), file=str(archive_path), remediation="Re-export a valid unencrypted ZIP archive.") from exc


def _validate_bundle(root: Path, temporary_root: Path | None, repository_root: Path, safety: dict[str, Any]) -> Bundle:
    if not root.is_dir():
        raise _error("BUNDLE_ROOT", "bundle root is not a directory", file=str(root), remediation="Provide a directory containing manifest.yaml.")
    max_file = int(safety.get("max_bundle_file_bytes", 1024 * 1024))
    max_files = int(safety.get("max_bundle_files", 64))
    allowed_extensions = {str(item).lower() for item in safety.get("allowed_text_extensions", [])}
    actual_files: dict[str, Path] = {}
    total_bytes = 0
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        safe_relative_path(relative, max_bytes=int(safety.get("max_path_bytes", 240)), max_depth=int(safety.get("max_path_depth", 8)))
        if candidate.is_symlink():
            raise _error("BUNDLE_SYMLINK", "bundle contains a symlink", file=str(root), location=relative, remediation="Copy regular text files into the bundle.")
        if not candidate.is_file() and not candidate.is_dir():
            raise _error("BUNDLE_SPECIAL_FILE", "bundle contains a special file", file=str(root), location=relative, remediation="Copy only regular text files into the bundle.")
        if not candidate.is_file():
            continue
        if candidate.name in IGNORED_FILESYSTEM_METADATA:
            # Google Drive/macOS may materialize an empty Icon\r sidecar in
            # synchronized directories. It is not part of the wire bundle.
            continue
        if Path(relative).suffix.lower() not in allowed_extensions:
            raise _error("BUNDLE_EXTENSION", "bundle contains a forbidden file extension", file=str(root), location=relative, remediation="Use only the configured text extensions.")
        size = candidate.stat().st_size
        if size > max_file:
            raise _error("BUNDLE_FILE_TOO_LARGE", f"file exceeds {max_file} bytes", file=str(root), location=relative, remediation="Keep bundle files below the configured limit.")
        total_bytes += size
        actual_files[relative] = candidate
    if len(actual_files) > max_files + 1:
        raise _error("BUNDLE_FILE_COUNT", "bundle contains too many files", file=str(root), remediation="Reduce the bundle to its manifest-declared text files.")
    if total_bytes > int(safety.get("max_bundle_bytes", 8 * 1024 * 1024)):
        raise _error("BUNDLE_TOO_LARGE", "bundle exceeds the configured uncompressed size limit", file=str(root), remediation="Export a smaller text-only bundle.")

    manifest_path = root / "manifest.yaml"
    manifest = load_yaml(manifest_path)
    if not isinstance(manifest, dict):
        raise _error("MANIFEST_OBJECT_REQUIRED", "manifest.yaml must contain a YAML object", file=str(manifest_path), remediation="Create a mapping manifest.")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise _error("MANIFEST_FILES_REQUIRED", "manifest.files must be a list", file=str(manifest_path), remediation="List every bundle file except manifest.yaml with its raw hash.")
    declared: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise _error("MANIFEST_FILE_ENTRY", "each manifest.files item must contain a path", file=str(manifest_path), location=f"/files/{index}", remediation="Add path, size_bytes, and sha256 to every file entry.")
        relative = safe_relative_path(entry["path"], max_bytes=int(safety.get("max_path_bytes", 240)), max_depth=int(safety.get("max_path_depth", 8)))
        if relative == "manifest.yaml" or relative in declared:
            raise _error("MANIFEST_DUPLICATE_PATH", f"manifest repeats or includes forbidden path {relative!r}", file=str(manifest_path), location=f"/files/{index}/path", remediation="List each payload path once and omit manifest.yaml.")
        declared[relative] = entry
    actual_payload = set(actual_files) - {"manifest.yaml"}
    if set(declared) != actual_payload:
        raise _error("MANIFEST_FILE_SET", "manifest files do not exactly match bundle files", file=str(manifest_path), remediation="Regenerate manifest.yaml after adding or removing files.")
    canonical_entries: list[dict[str, Any]] = []
    for relative in sorted(declared):
        entry = declared[relative]
        raw = actual_files[relative].read_bytes()
        expected_sha = sha256_bytes(raw)
        if entry.get("size_bytes") != len(raw) or entry.get("sha256") != expected_sha:
            raise _error("MANIFEST_FILE_HASH", f"raw hash or size mismatch for {relative}", file=str(manifest_path), location=f"/files/{relative}", remediation="Recalculate manifest file hashes from the exported bytes.")
        canonical_entries.append({"path": relative, "size_bytes": len(raw), "sha256": expected_sha})
        try:
            read_text(actual_files[relative])
        except DiagnosticError:
            raise
    expected_file_set = canonical_sha256(canonical_entries)
    actual_file_set = ((manifest.get("integrity") or {}).get("file_set_sha256") if isinstance(manifest.get("integrity"), dict) else None)
    if actual_file_set != expected_file_set:
        raise _error("MANIFEST_FILE_SET_HASH", "manifest file set hash does not match payload files", file=str(manifest_path), location="/integrity/file_set_sha256", remediation="Recalculate manifest.integrity.file_set_sha256.")

    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, str) or entrypoint not in declared:
        raise _error("MANIFEST_ENTRYPOINT", "manifest entrypoint is not a declared payload file", file=str(manifest_path), location="/entrypoint", remediation="Set entrypoint to production-handoff.yaml and declare it in files.")
    handoff_path = root / Path(entrypoint)
    handoff = load_yaml(handoff_path)
    if not isinstance(handoff, dict):
        raise _error("HANDOFF_OBJECT_REQUIRED", "handoff entrypoint must contain a YAML object", file=str(handoff_path), remediation="Create a mapping handoff document.")
    key = manifest.get("handoff_key")
    if not isinstance(key, dict) or key.get("handoff_id") != handoff.get("handoff_id") or key.get("revision") != handoff.get("revision"):
        raise _error("HANDOFF_KEY_MISMATCH", "manifest handoff_key does not match the handoff entrypoint", file=str(manifest_path), location="/handoff_key", remediation="Regenerate the manifest from the same handoff ID and revision.")
    schema_path = root / "schemas/production-handoff.schema.json"
    provenance_path = root / "provenance.yaml"
    if "schemas/production-handoff.schema.json" not in declared or not schema_path.is_file():
        raise _error("HANDOFF_SCHEMA_MISSING", "bundle does not contain its handoff schema snapshot", file=str(manifest_path), remediation="Export schemas/production-handoff.schema.json with the handoff.")
    if "provenance.yaml" not in declared or not provenance_path.is_file():
        raise _error("PROVENANCE_MISSING", "bundle does not contain provenance.yaml", file=str(manifest_path), remediation="Export provenance.yaml with source commit and schema hash.")
    schema = load_schema(schema_path)
    bundled_common_path = root / "schemas/common.schema.json"
    common = load_schema(bundled_common_path) if bundled_common_path.is_file() else load_schema(repository_root / "schemas/common.schema.json")
    schema_findings = validate_instance(handoff, schema, schema_path=schema_path, common_schema=common)
    if schema_findings:
        raise DiagnosticError(schema_findings[0])
    if handoff.get("status") != "READY":
        raise _error("HANDOFF_STATUS", "only READY handoffs can be received", file=str(handoff_path), location="/status", remediation="Complete research-side validation and export a READY handoff.")
    actual_handoff_hash = ((handoff.get("integrity") or {}).get("content_sha256") if isinstance(handoff.get("integrity"), dict) else None)
    expected_handoff_hash = handoff_sha256(handoff)
    if actual_handoff_hash != expected_handoff_hash:
        raise _error("HANDOFF_HASH_MISMATCH", "canonical handoff payload hash does not match", file=str(handoff_path), location="/integrity/content_sha256", remediation="Recalculate the handoff hash with the canonical serializer.")
    if len(__import__("json").dumps({key: value for key, value in handoff.items() if key != "integrity"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) > int(safety.get("max_handoff_payload_bytes", 262144)):
        raise _error("HANDOFF_PAYLOAD_TOO_LARGE", "canonical handoff payload exceeds the configured limit", file=str(handoff_path), remediation="Move raw or large data behind approved opaque references.")
    provenance = load_yaml(provenance_path)
    if not isinstance(provenance, dict):
        raise _error("PROVENANCE_OBJECT_REQUIRED", "provenance.yaml must contain a YAML object", file=str(provenance_path), remediation="Create a provenance mapping.")
    _validate_provenance(provenance, root, declared)
    manifest_security = check_text_security(manifest, file="manifest.yaml", forbidden_markers=safety.get("forbidden_markers", []), signed_url_markers=safety.get("signed_url_markers", []))
    if manifest_security:
        raise DiagnosticError(manifest_security[0])
    for relative in sorted(declared):
        text = read_text(actual_files[relative])
        if relative.startswith("schemas/"):
            # Schema snapshots intentionally contain policy vocabulary such
            # as PRIVATE_RAW and RESTRICTED; they are contract data, not
            # project payload.
            continue
        security_findings = check_text_security(
            text,
            file=relative,
            forbidden_markers=safety.get("forbidden_markers", []),
            signed_url_markers=safety.get("signed_url_markers", []),
        )
        if security_findings:
            raise DiagnosticError(security_findings[0])
    return Bundle(root, temporary_root, manifest, handoff, schema, provenance, tuple(sorted(declared)))


def _validate_provenance(provenance: dict[str, Any], root: Path, declared: dict[str, Any]) -> None:
    source_commit = provenance.get("source_commit")
    if not isinstance(source_commit, str) or not __import__("re").fullmatch(r"[0-9a-f]{40}", source_commit):
        raise _error("PROVENANCE_COMMIT", "provenance.source_commit must be a 40-character lowercase SHA", file="provenance.yaml", location="/source_commit", remediation="Export the clean source commit that generated the handoff.")
    if provenance.get("source_tree_clean") is not True:
        raise _error("PROVENANCE_DIRTY_SOURCE", "bundle source_tree_clean must be true for acceptance", file="provenance.yaml", location="/source_tree_clean", remediation="Export from a clean source commit or mark the bundle as development-only.")
    source_schema = provenance.get("source_schema")
    if not isinstance(source_schema, dict) or not isinstance(source_schema.get("path"), str) or not isinstance(source_schema.get("sha256"), str):
        raise _error("PROVENANCE_SCHEMA", "provenance.source_schema must include path and sha256", file="provenance.yaml", location="/source_schema", remediation="Record the source schema path and raw SHA-256.")
    relative = safe_relative_path(source_schema["path"])
    if relative not in declared:
        raise _error("PROVENANCE_SCHEMA_PATH", "provenance schema path is not declared in the bundle", file="provenance.yaml", location="/source_schema/path", remediation="Declare and include the source schema snapshot.")
    actual = sha256_bytes((root / Path(relative)).read_bytes())
    if actual != source_schema["sha256"]:
        raise _error("PROVENANCE_SCHEMA_HASH", "source schema raw hash does not match provenance", file="provenance.yaml", location="/source_schema/sha256", remediation="Recalculate provenance from the exported schema bytes.")
