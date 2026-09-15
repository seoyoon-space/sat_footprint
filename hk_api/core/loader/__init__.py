from importlib import import_module

__all__ = ["HKLoader"]


def __getattr__(name: str):
    if name == "HKLoader":
        return import_module(".hk_loader", __name__).HKLoader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
