"""Benchmark Unicode-safe WER/CER using JiWER, without ASR imports."""

import unicodedata

from backend.config import Language

NORMALIZATION = "nfkc-casefold-punctuation-v1"


def normalize(text: str, language: str) -> str:
    language = Language(language)
    text = unicodedata.normalize("NFKC", text).casefold()
    if language == Language.CHINESE:
        # CER includes letters/digits too, but excludes punctuation and whitespace.
        return "".join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P"))
    # Apostrophes inside contractions are removed; other punctuation separates words.
    text = "".join("" if c in {"'", "’"} else " " if unicodedata.category(c).startswith("P") else c for c in text)
    return " ".join(text.split())


def score_accuracy(language: str, reference: str | None, hypothesis: str, *, verified: bool) -> dict:
    language = Language(language).value
    metric = "CER" if language == "Chinese" else "WER"
    result = {"status": "NOT MEASURED", "metric": metric, "error_rate": None,
              "reference": reference, "hypothesis": hypothesis, "normalization": NORMALIZATION}
    if not verified or reference is None or not reference.strip():
        result["reason"] = "Reference missing, empty, or not verified."
        return result
    ref, hyp = normalize(reference, language), normalize(hypothesis, language)
    result.update(normalized_reference=ref, normalized_hypothesis=hyp)
    if not ref:
        result["reason"] = "Reference is empty after normalization; denominator is undefined."
        return result
    from jiwer import process_characters, process_words

    measured = process_characters(ref, hyp) if metric == "CER" else process_words(ref, hyp)
    count = len(ref) if metric == "CER" else len(ref.split())
    result.update(status="MEASURED", error_rate=measured.cer if metric == "CER" else measured.wer,
                  substitutions=measured.substitutions, deletions=measured.deletions,
                  insertions=measured.insertions, hits=measured.hits, reference_units=count)
    return result
