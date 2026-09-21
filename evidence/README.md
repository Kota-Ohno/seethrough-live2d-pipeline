# 制作の観測記録

[2026-09-20〜21 月繭こよみ](koyomi-20260920-21/README.md)は、制作中の構造化レポートを匿名化して保存したスナップショットです。[知見の要約](../docs/rigging-runtime-recording-lessons.md)と併用してください。

記録中の `status` や `pending` は、その記録が書かれた時点の主張です。古い失敗を後の合格で上書きせず、未検証の工程を合格へ変更しません。ファイルのmtime順を、厳密なイベント順・原因と結果の順として扱わないでください。

次回は `scripts/export_observability.py` に対象プロジェクトのディレクトリを明示して別の出力先へ保存します。既存スナップショットは上書きしません。公開前に内容を確認してください。自動匿名化だけで秘密情報の不存在を保証するものではありません。

```sh
python scripts/export_observability.py \
  --source deliverables=/path/to/project/outputs \
  --source scratch=/path/to/project/work \
  --source native-stage=/path/to/native/stage \
  --output evidence/new-run --run-id new-run
python scripts/validate_observability.py evidence/new-run --index evidence/new-run/index.jsonl
```

`scratch` と `native-stage` は直下のファイルだけを対象とします。それ以外のラベルは再帰的に走査するため、アカウント全体やホームディレクトリを指定しないでください。64MiBを超えるテキスト、バイナリ、コード、VTS個人設定、motion3曲線、認証情報を示すファイル名は内容を収録せず、理由付きで一覧化します。個人の設定や生のトラッキング曲線を収録したい場合は公開範囲を改めて検討してください。

## データ形式 v1

| ファイル | 内容 |
|---|---|
| `records.jsonl.gz` | UTF-8 JSONLをgzip圧縮。1行が1つの元ファイルに対応し、`payload` に匿名化した元内容を保持 |
| `inventory.jsonl` | 発見した全対象ファイルのメタデータ。収録・除外・理由・元のサイズ・mtimeを記録 |
| `index.jsonl` | 検証スクリプトが作る軽量索引。報告済みstatusなどと元payloadのトップレベルキー |
| `manifest.json` | 収録数、除外理由ごとの数、匿名化数、取得時刻、アーカイブのSHA-256 |

各recordの必須項目は `schema_version`, `record_id`, `run_id`, `source`, `path`, `source_sha256`, `source_bytes`, `source_mtime_utc`, `exported_at_utc`, `payload_kind`, `payload_sha256`, `redactions`, `evidence_semantics`, `payload`。

- `record_id` はsourceラベルと相対パスから生成。内容の識別にはSHA-256を使う。
- `source_sha256` は非公開の元ファイルのバイト列、`payload_sha256` は匿名化後のpayloadを `ensure_ascii=False, sort_keys=True, separators=(',', ':')` でJSON化したUTF-8列のハッシュ。
- `payload_kind` は `json` / `jsonl` / `text`。JSONL元ファイルはpayload内の配列になる。
- `source_mtime_utc` はファイル更新時刻、`exported_at_utc` は収集時刻。イベント日時が元の `at` 等にあればpayload内にそのまま保持する。
- `redactions` は置換の種類と回数。`${deliverables}` 等は元ルートの別名、`[LOCAL_PATH]` 等は公開から除いた値。
- 同じ内容が配備先と作業場所にあれば別recordにする。重複は `source_sha256` で判定できる。
- バイナリ除外ファイルは内容を読まずサイズとmtimeだけを記録する。ハッシュがないので同一性は保証しない。元のmanifestに記載された画像等のハッシュは、履歴としてのみ残る。

Pythonで個別の失敗を読む例：

```python
import gzip, json
with gzip.open('evidence/koyomi-20260920-21/records.jsonl.gz', 'rt', encoding='utf-8') as f:
    for line in f:
        record = json.loads(line)
        if 'runtime-resume' in record['path']:
            print(json.dumps(record, ensure_ascii=False, indent=2))
```

収録時の検証はファイルハッシュ、全payloadのハッシュ、索引とinventoryの対応、レコード数、重複ID、既知の秘密情報パターンです。モデルの外観や動作を再検証する処理ではありません。
