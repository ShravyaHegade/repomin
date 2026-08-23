from pathlib import Path

from fastapi import FastAPI


app = FastAPI()


def failure_message() -> str:
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    root_requirements = Path("requirements.txt").read_text(encoding="utf-8")
    runtime_requirements = Path("requirements/runtime.txt").read_text(
        encoding="utf-8"
    )
    message = "DIFFERENT_FAILURE"
    if (
        "fastapi==0.116.1" in project
        and "-r requirements/runtime.txt" in root_requirements
        and "fastapi==0.116.1" in runtime_requirements
    ):
        message = "FastAPI route regression: dependency override leaked"
    return message


@app.get("/checkout/{order_id}")
def checkout(order_id: int) -> dict:
    if order_id == 42:
        raise RuntimeError(failure_message())
    return {"order_id": order_id, "status": "ok"}
