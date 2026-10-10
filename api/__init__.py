from fastapi.responses import JSONResponse


def success_response(data=None, status_code: int = 200):
    return JSONResponse(
        status_code=status_code,
        content={"success": True, "data": data if data is not None else {}},
    )


def error_response(code: str, message: str, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": code,
                "message": message,
            },
        },
    )
