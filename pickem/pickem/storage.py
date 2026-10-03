from urllib.parse import unquote, urlsplit

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class HashedStaticFilesStorage(ManifestStaticFilesStorage):
    """Serve content-hashed static URLs after collectstatic.

    Django skips hashes while DEBUG is True. This project keeps DEBUG on in
    production, so look up the manifest anyway. With no manifest (local
    runserver, before collectstatic), keep the original filename.
    """

    manifest_strict = False

    def url(self, name, force=False):
        parsed_name = urlsplit(unquote(name))
        clean_name = parsed_name.path.strip()
        if self.hashed_files.get(self.hash_key(clean_name)):
            force = True
        return super().url(name, force=force)
