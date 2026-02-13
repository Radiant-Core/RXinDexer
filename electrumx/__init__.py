version = 'ElectrumX 1.3.0'
version_short = version.split()[-1]


def _lazy_import(name):
    """Lazy import to avoid pulling in aiorpcx at module load time."""
    import importlib
    if name == 'Controller':
        mod = importlib.import_module('electrumx.server.controller')
        return mod.Controller
    if name == 'Env':
        mod = importlib.import_module('electrumx.server.env')
        return mod.Env
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __getattr__(name):
    return _lazy_import(name)
