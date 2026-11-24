"""Download ONE file from a Kaggle model version (the CLI only downloads whole versions).
Usage: kaggle_model_file.py owner/model/framework/instance/version FILE OUT_DIR"""
import os, sys
from pathlib import Path
sys.argv, (handle, fname, out) = sys.argv[:1], sys.argv[1:4]
exec((Path(__file__).parent / "kaggle_cli.py").read_text().split("from kaggle.cli")[0])
from kaggle import api  # noqa: E402
from kagglesdk.models.types.model_api_service import ApiDownloadModelInstanceVersionRequest  # noqa: E402
from kagglesdk.models.types.model_enums import ModelFramework  # noqa: E402

owner, model, fw, inst, ver = handle.split("/")
r = ApiDownloadModelInstanceVersionRequest()
r.owner_slug, r.model_slug, r.instance_slug, r.version_number = owner, model, inst, int(ver)
r.framework = getattr(ModelFramework, f"MODEL_FRAMEWORK_{fw.upper()}")
r.path = fname
with api.build_kaggle_client() as k:
    resp = k.models.model_api_client.download_model_instance_version(r)
    dest = Path(out) / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(1 << 16):
            f.write(chunk)
print(dest, dest.stat().st_size)
