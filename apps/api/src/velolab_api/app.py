from fastapi import FastAPI

app = FastAPI(title="velolab-api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
