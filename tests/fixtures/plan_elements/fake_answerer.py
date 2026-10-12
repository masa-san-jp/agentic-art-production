"""Deterministic Stage D answers for tests: one value per element-request/v1, synthetic only."""

SPECS = {
    'dimensions': '板は1枚あたり幅 600 mm・高さ 900 mm・厚さ 5.5 mm で、12枚を間隔 300 mm で横一列に並べ全長約 10.5 m とする。',
    'materials': 'シナ合板 5.5 mm 厚の白い下地塗装板と、黒の水性ペン（線幅 1 mm）を使う。',
    'assembly': '各板の裏に吊り金具を2個ずつネジ留めし、壁のピクチャーレールからワイヤーで吊る。',
    'installation': '展示室の壁面に床から板の下端まで 800 mm の高さで、番号順に左から右へ吊る。',
    'format': 'SVG のベクター画像と PNG のプレビュー画像として書き出す。',
    'resolution': '1920 × 1080 ピクセルの横長画面で表示する。',
    'duration': '静止画のため再生時間は 0 秒で、展示期間中は常に表示する。',
    'software': 'Inkscape 1.3 で作図し、プレビューはブラウザで確かめる。',
    'delivery': '展示室のモニター1台に表示し、同じ画像をウェブページでも公開する。',
}


def answer(request):
    identifier = request['element_id']
    fmt = request['answer_format']
    envelope = {'contract_version': 'element-answer/v1', 'run_id': request['run_id'],
                'element_id': identifier, 'attempt': request['attempt']}
    parts = identifier.split('.')
    if fmt['type'] == 'choice':
        value = 'physical' if identifier == 'medium' else fmt['choices'][0]
    elif identifier == 'environment.location':
        value = '屋内の展示室で、幅 12 m の白い壁面と天井のピクチャーレール、床のコンセントを使える。'
    elif parts[0] == 'spec':
        value = SPECS[parts[1]]
    elif parts[0] == 'step':
        task = parts[1]
        value = {
            'instruction': f'{task} の作業として、指示書の内容に沿って材料と道具を作業台に並べ、決めた寸法どおりに一つずつ進める。',
            'completion': f'{task} の結果を作業の位置から撮影し、決めた寸法と数が写真で数えられれば完了とする。',
            'material': 'シナ合板 5.5 mm 厚 600×900 mm',
            'material-quantity': '12 sheet',
            'tool': '電動ドライバーと水平器',
        }[parts[2]]
    elif parts[0] == 'gap':
        gap = parts[1]
        value = {
            'method': f'{gap} について、制作担当が関係する記録を読み直し、足りない情報を担当者に確認して計画に書き足す。',
            'condition': f'{gap} の内容が確認済みとして記録され、計画を作り直しても同じ不足が出ないこと。',
        }[parts[2]]
    else:
        raise ValueError('Unknown fake Stage D element ' + identifier)
    return {**envelope, 'value': value}
