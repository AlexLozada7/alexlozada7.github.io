# -*- coding: utf-8 -*-
"""Genera macro-data.json para la pagina de contexto macroeconomico.

Corre una vez al dia desde GitHub Actions. No escribe HTML: la pagina es
estatica y lee este archivo al cargar. Asi, si un dia una fuente falla, la
pagina sigue mostrando la ultima lectura buena con su fecha en vez de
quedarse en blanco -- que es lo peor que puede hacer un tablero de datos.

Estados Unidos sale de FRED por su CSV publico, sin llave.
Costa Rica sale del web service del BCCR, que si exige correo y token; los
toma de las variables de entorno BCCR_EMAIL y BCCR_TOKEN. Sin ellas, el
bloque de Costa Rica simplemente no se actualiza y conserva lo anterior.
"""
import csv
import datetime as dt
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(os.path.dirname(AQUI), "macro-data.json")
UA = {"User-Agent": "alexlozada7.github.io macro brief"}


def baja(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ── FRED ─────────────────────────────────────────────────────────────
def serie_fred(sid):
    """Devuelve [(fecha, valor)] ascendente, saltando los huecos ('.')."""
    texto = baja("https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + sid)
    filas = []
    for fila in csv.reader(io.StringIO(texto)):
        if len(fila) < 2 or not fila[0][:1].isdigit():
            continue
        try:
            filas.append((fila[0], float(fila[1])))
        except ValueError:
            continue          # '.' es como FRED marca un dato ausente
    return filas


def ultimo(sid):
    s = serie_fred(sid)
    if not s:
        raise ValueError("serie vacia: " + sid)
    return s[-1]


def inflacion_interanual(sid="CPIAUCSL"):
    """El CPI viene en indice, no en variacion: la tasa hay que calcularla."""
    s = serie_fred(sid)
    if len(s) < 13:
        raise ValueError("serie corta para interanual")
    f, hoy = s[-1]
    _, hace_un_anio = s[-13]
    return f, (hoy / hace_un_anio - 1) * 100


# ── BCCR ─────────────────────────────────────────────────────────────
BCCR_WS = ("https://gee.bccr.fi.cr/Indicadores/Suscripciones/WS/"
           "wsindicadoreseconomicos.asmx/ObtenerIndicadoresEconomicosXML")


def serie_bccr(codigo, dias=20):
    correo = os.environ.get("BCCR_EMAIL", "").strip()
    token = os.environ.get("BCCR_TOKEN", "").strip()
    if not (correo and token):
        raise RuntimeError("sin BCCR_EMAIL / BCCR_TOKEN")
    fin = dt.date.today()
    ini = fin - dt.timedelta(days=dias)
    q = urllib.parse.urlencode({
        "Indicador": codigo,
        "FechaInicio": ini.strftime("%d/%m/%Y"),
        "FechaFinal": fin.strftime("%d/%m/%Y"),
        "Nombre": "Alexandre Lozada",
        "SubNiveles": "N",
        "CorreoElectronico": correo,
        "Token": token,
    })
    xml = baja(BCCR_WS + "?" + q)
    import re
    pares = re.findall(r"<DES_FECHA>([^<]+)</DES_FECHA>\s*<NUM_VALOR>([^<]+)</NUM_VALOR>", xml)
    if not pares:
        raise ValueError("el BCCR no devolvio datos para " + str(codigo))
    f, v = pares[-1]
    return f[:10], float(v)


# ── Que se publica, y como se explica ────────────────────────────────
US = [
    ("fed", "Tasa de fondos federales", "%", lambda: ultimo("FEDFUNDS"),
     "Es el precio del dinero en Estados Unidos. No le cobra a usted directamente, pero marca el piso "
     "de casi todo lo demas: cuando sube, sube el costo de prestar en dolares y bajan las valoraciones "
     "de lo que promete ganancias lejanas."),
    ("cpi", "Inflacion interanual de Estados Unidos", "%", inflacion_interanual,
     "Cuanto subieron los precios alla en doce meses. Importa aqui porque si usted ahorra en dolares, "
     "esta es la tasa a la que ese ahorro pierde poder de compra mientras esta quieto."),
    ("unrate", "Desempleo en Estados Unidos", "%", lambda: ultimo("UNRATE"),
     "Un desempleo muy bajo sostiene el consumo pero presiona salarios y precios. Uno que sube rapido "
     "suele anticipar menos demanda, y en Costa Rica eso se siente primero en zona franca y turismo."),
    ("dgs10", "Tesoro a 10 anos", "%", lambda: ultimo("DGS10"),
     "El rendimiento de referencia a largo plazo. Es el numero contra el que se compara cualquier "
     "inversion: si un bono del gobierno mas seguro del mundo paga esto, cualquier otra cosa tiene que "
     "pagar mas para compensar su riesgo."),
    ("dgs2", "Tesoro a 2 anos", "%", lambda: ultimo("DGS2"),
     "Refleja lo que el mercado espera de la politica monetaria en el corto plazo. Cuando supera al de "
     "10 anos, la curva se invierte, que historicamente ha precedido recesiones sin ser una garantia."),
    ("vix", "VIX", "indice", lambda: ultimo("VIXCLS"),
     "La volatilidad que el mercado espera para los proximos treinta dias. Bajo 15 es calma; sobre 30 "
     "es miedo. Sirve para entender el ambiente, no para decidir: a nadie le ha ido bien cronometrando "
     "el mercado con este numero."),
]

# Codigos del catalogo del BCCR. 317 compra y 318 venta del dolar; los demas
# quedan declarados para cuando exista el token y se puedan verificar.
CR = [
    ("tc_compra", "Tipo de cambio, compra", "CRC", 317,
     "El precio al que le compran sus dolares. Si usted gana en dolares y gasta en colones, este numero "
     "le cambia el salario real todos los meses sin que usted haga nada."),
    ("tc_venta", "Tipo de cambio, venta", "CRC", 318,
     "El precio al que usted compra dolares. La diferencia con el de compra es el margen del banco, y "
     "sobre montos grandes deja de ser un detalle."),
    ("tpm", "Tasa de politica monetaria", "%", 3541,
     "La tasa con la que el Banco Central mueve el resto. Cuando sube, suben las tasas de sus creditos "
     "en colones, y con retraso tambien lo que le pagan por ahorrar."),
    ("tbp", "Tasa basica pasiva", "%", 423,
     "El referente al que estan atados muchos creditos en colones en Costa Rica. Si su hipoteca dice "
     "'TBP mas tres puntos', este es el numero que decide su cuota."),
]


def main():
    previo = {}
    if os.path.exists(SALIDA):
        try:
            previo = json.load(io.open(SALIDA, encoding="utf-8"))
        except Exception:
            previo = {}
    antes = {i["id"]: i for i in previo.get("indicadores", [])}

    fuera, fallos = [], []

    for ident, nombre, unidad, traer, nota in US:
        try:
            fecha, valor = traer()
            fuera.append({"id": ident, "n": nombre, "u": unidad, "v": round(valor, 2),
                          "f": fecha, "z": "us", "d": nota})
        except Exception as e:
            fallos.append("%s: %s" % (ident, e))
            if ident in antes:
                viejo = dict(antes[ident]); viejo["stale"] = True
                fuera.append(viejo)

    for ident, nombre, unidad, codigo, nota in CR:
        try:
            fecha, valor = serie_bccr(codigo)
            fuera.append({"id": ident, "n": nombre, "u": unidad, "v": round(valor, 2),
                          "f": fecha, "z": "cr", "d": nota})
        except Exception as e:
            fallos.append("%s: %s" % (ident, e))
            if ident in antes:
                viejo = dict(antes[ident]); viejo["stale"] = True
                fuera.append(viejo)

    if not fuera:
        print("macro: ninguna fuente respondio, no se escribe nada")
        return 1

    doc = {"generado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "indicadores": fuera}
    if fallos:
        doc["fallos"] = fallos
    with io.open(SALIDA, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write(u"\n")
    print("macro: %d indicadores escritos%s"
          % (len(fuera), (", %d fuentes fallaron" % len(fallos)) if fallos else ""))
    for f in fallos:
        print("  - " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
