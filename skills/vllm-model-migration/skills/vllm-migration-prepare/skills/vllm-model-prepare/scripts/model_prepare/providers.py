"""仅在实际远程请求时加载对应的官方 SDK 适配器。"""
import importlib
import inspect
import re


def sdk_version(module):
    return getattr(module, "__version__", "unknown")


class HuggingFaceSource:
    def __init__(self, request):
        self.sdk = importlib.import_module("huggingface_hub")
        self.api = self.sdk.HfApi()
        self.request = request
        self.revision = request["revision"]

    def describe(self):
        info = self.api.model_info(self.request["repository"], revision=self.revision,
                                   files_metadata=True, timeout=30)
        if not isinstance(info.sha, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", info.sha):
            raise ValueError("Hugging Face 未返回不可变的提交身份")
        self.revision = info.sha
        files = []
        for item in info.siblings:
            lfs = item.lfs
            digest = getattr(lfs, "sha256", None) if lfs is not None else None
            if isinstance(lfs, dict):
                digest = lfs.get("sha256")
            files.append({"path": item.rfilename, "size": item.size, "sha256": digest,
                          "git_blob_sha1": item.blob_id if lfs is None else None, "revision": info.sha})
        return {"provider": "huggingface", "repository": self.request["repository"],
                "requested_revision": self.request["revision"], "resolved_revision": self.revision,
                "revision_kind": "commit", "sdk_version": sdk_version(self.sdk), "source_files": files}

    def download(self, selected):
        return self.sdk.snapshot_download(repo_id=self.request["repository"], revision=self.revision,
                                          local_dir=self.request["path"], allow_patterns=selected,
                                          max_workers=self.request["max_workers"])


class ModelScopeSource:
    def __init__(self, request):
        self.sdk = importlib.import_module("modelscope")
        self.api = importlib.import_module("modelscope.hub.api").HubApi()
        self.request = request
        self.hub = None
        self.hub_version = None
        if "revision" not in inspect.signature(self.api.get_model_files).parameters:
            hub_sdk = importlib.import_module("modelscope_hub")
            self.hub = hub_sdk.HubApi()
            self.hub_version = sdk_version(hub_sdk)

    def describe(self):
        records = self.file_records()
        return {"provider": "modelscope", "repository": self.request["repository"],
                "requested_revision": self.request["revision"], "resolved_revision": self.request["revision"],
                "revision_kind": "branch_or_tag", "sdk_version": sdk_version(self.sdk),
                "hub_sdk_version": self.hub_version, "api_family": "modelscope_hub" if self.hub else "modelscope",
                "file_revision_available": all(bool(row["revision"]) for row in records), "source_files": records}

    def file_records(self):
        if self.hub:
            files = self.hub.list_repo_files(self.request["repository"], "model", revision=self.request["revision"], recursive=True)
            return [{"path": row.path, "size": row.size, "sha256": row.sha256, "revision": getattr(row, "revision", None)}
                    for row in files if row.type == "blob"]
        files = self.api.get_model_files(self.request["repository"], revision=self.request["revision"], recursive=True)
        return [{"path": row["Path"], "size": row.get("Size"), "sha256": row.get("Sha256"),
                 "revision": row.get("Revision")} for row in files if row.get("Type") == "blob"]

    def download(self, selected):
        if self.hub:
            return self.hub.download_repo(self.request["repository"], "model", revision=self.request["revision"],
                                         local_dir=self.request["path"], allow_patterns=selected,
                                         max_workers=self.request["max_workers"])
        return self.sdk.snapshot_download(model_id=self.request["repository"], revision=self.request["revision"],
                                          local_dir=self.request["path"], allow_patterns=selected,
                                          max_workers=self.request["max_workers"])
