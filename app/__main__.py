"""Arranque del servidor: `python -m app`.

El puerto se lee acá desde la variable de entorno PORT (la inyecta Railway).
Hacerlo en Python, y no con "$PORT" en la línea de comandos, evita depender
de que una shell expanda la variable: Railway ejecuta el comando de arranque
sin shell, y uvicorn recibía el texto literal "$PORT".
"""
import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
