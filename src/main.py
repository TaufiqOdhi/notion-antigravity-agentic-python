from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="FastAPI Main",
    description="Main entry point for the FastAPI application.",
    version="1.0.0"
)

class HealthResponse(BaseModel):
    status: str

@app.get("/", response_model=HealthResponse)
def read_root():
    return {"status": "ok"}

@app.get("/health", response_model=HealthResponse)
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
