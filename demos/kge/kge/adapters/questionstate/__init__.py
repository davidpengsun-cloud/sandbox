"""Question-run storage selected independently from identity and onboarding state."""
from kge.adapters.objstore import open_store, under
from kge.pipeline.settings import settings
from kge.ports import QuestionRuns


def for_profile(prof) -> QuestionRuns:
    backend = (settings().QUESTION_STATE_BACKEND or "store").strip().lower()
    if backend == "store":
        from kge.adapters.questionstate.store import StoreQuestionRuns
        url = (settings().STORE_URL or "").strip()
        target = under(url, "questions", prof.client, "runs") if url else str((prof.run_dir() / "question-runs").resolve())
        return StoreQuestionRuns(open_store(target, private=True))
    if backend == "bigquery":
        from kge.adapters.questionstate.bigquery import BigQueryQuestionRuns
        return BigQueryQuestionRuns(prof)
    raise ValueError("QUESTION_STATE_BACKEND must be store or bigquery")
