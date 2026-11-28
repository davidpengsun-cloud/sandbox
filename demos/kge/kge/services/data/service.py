import sys

from kge.services.data.engine import service as implementation

sys.modules[__name__] = implementation
