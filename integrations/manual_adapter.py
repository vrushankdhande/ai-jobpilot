"""Source ``manual``: a job the user found anywhere and typed/pasted in. Nothing is fetched."""
from integrations.base_adapter import ManualOnlyAdapter


class ManualImportAdapter(ManualOnlyAdapter):
    name = "manual"
    host_suffixes = ()  # any public URL may be stored (it is never requested)
    id_patterns = ()
