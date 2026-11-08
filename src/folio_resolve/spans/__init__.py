"""Opt-in, deterministic FOLIO and OpenGloss span annotations.

Load a verified compiled lexicon with ``load_lexicon(artifact, manifest)`` and
an optional mapping set with ``load_crosswalk(sssom_path)``. Construct a
``SpanTagger`` once, then call ``tag(text).to_json()`` for reproducible output.
The runtime never imports pyarrow; the lexicon build CLI requires the extra.
"""

from .crosswalk import Crosswalk, load_crosswalk
from .lexicon import OpenGlossLexicon, load_lexicon, verify_lexicon
from .models import TagResult
from .tagger import SpanTagger

__all__ = [
    "Crosswalk",
    "OpenGlossLexicon",
    "SpanTagger",
    "TagResult",
    "load_crosswalk",
    "load_lexicon",
    "verify_lexicon",
]
