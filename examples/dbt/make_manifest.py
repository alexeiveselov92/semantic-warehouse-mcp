"""Make examples/dbt/manifest.json: `dbt parse` of the made-up shop's dbt project (examples/dbt/shop), in a folder of
its own, with what the importer reads kept — metadata without what identifies the run, nodes and sources.

    uv run python examples/dbt/make_manifest.py

dbt is not one of the server's dependencies: it runs through uvx, at the version the example was made with.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
DBT = ["uvx", "--quiet", "--from", "dbt-core==1.10.15", "--with", "dbt-duckdb==1.10.0", "dbt"]
KEPT = ("dbt_schema_version", "dbt_version", "project_name", "adapter_type")


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        project = pathlib.Path(folder) / "shop"
        shutil.copytree(HERE / "shop", project)
        subprocess.run([*DBT, "parse", "--profiles-dir", "."], cwd=project, check=True)
        manifest = json.loads((project / "target" / "manifest.json").read_text(encoding="utf-8"))
    kept = {
        "metadata": {key: manifest["metadata"][key] for key in KEPT},
        "nodes": manifest["nodes"],
        "sources": manifest["sources"],
    }
    (HERE / "manifest.json").write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {HERE / 'manifest.json'}: {len(kept['nodes'])} nodes, {len(kept['sources'])} sources")


if __name__ == "__main__":
    main()
