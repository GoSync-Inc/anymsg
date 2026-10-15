"""Keep the anymsg distributions self-contained while tests retain their paths."""

from pathlib import Path
import tempfile

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


ASSETS = (
    "bounds.mjs",
    "capture.mjs",
    "history.mjs",
    "identity.mjs",
    "ipc.mjs",
    "media.mjs",
    "navigation.mjs",
    "pair.mjs",
    "pairing.mjs",
    "provider.mjs",
    "sender.mjs",
    "outgoing.mjs",
    "transport-state.mjs",
    "read-state.mjs",
    "receipts.mjs",
    "operations.mjs",
    "package.json",
    "package-lock.json",
)


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        source = Path(self.root)
        # An unpacked sdist contains its assets; a checkout has the unchanged
        # collector subtree at repository root. Never scan runtime directories.
        repository = source if (source / "whatsapp").is_dir() else source.parents[1]
        force = build_data["force_include"]
        for name in ASSETS:
            path = repository / "whatsapp" / name
            if not path.is_file() or path.is_symlink():
                raise ValueError("missing reviewed WhatsApp build asset")
            target = (
                "messaging_service/whatsapp_assets"
                if self.target_name == "wheel"
                else "whatsapp"
            )
            force[str(path)] = target + "/" + name
        if self.target_name == "sdist":
            for name in (
                "SETUP.md",
                "media_benchmark.py",
                "client_check.py",
                "licenses/age-LICENSE",
            ):
                force[str(repository / name)] = name
            # Docker's source archive layout remains the published anymsg layout.
            self._temporary = tempfile.TemporaryDirectory(prefix="anymsg-build-")
            docker = Path(self._temporary.name) / "Dockerfile"
            docker.write_text(
                (source / "Dockerfile").read_text().replace("services/messaging/", "")
            )
            force[str(docker)] = "Dockerfile"

    def finalize(self, version, build_data, artifact_path):
        if hasattr(self, "_temporary"):
            self._temporary.cleanup()
