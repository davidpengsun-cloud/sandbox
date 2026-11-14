"""Read-only assistance using catalogue, Search and governed Ask."""


def assist(*args, **kwargs) -> dict:
    from kge.services.assist.service import assist as _assist
    return _assist(*args, **kwargs)


__all__ = ["assist"]
