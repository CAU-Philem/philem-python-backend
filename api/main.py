from fastapi import FastAPI

# import your router module (adjust filename!)
from api.routes.listing_routes import router as listings_router

app = FastAPI(title="PHILEM Python Backend")

# If your router already has full paths like "/listings/from-url"
app.include_router(listings_router)

# Optional: quick check endpoint
@app.get("/health")
def health():
    return {"status": "ok"}
