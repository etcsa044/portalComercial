"""
╔══════════════════════════════════════════════════════════════════════╗
║  SISTEMA DE AUDITORÍA DE VENTAS Y RETENCIÓN - v3 FINAL             ║
║  Streamlit + Pandas + SQLite3 + FPDF2 + Plotly                     ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import streamlit as st
import pandas as pd
import sqlite3
import os
import io
import tempfile
from datetime import datetime, date
from fpdf import FPDF
import plotly.express as px
import plotly.graph_objects as go

# ─────────────────────────────────────────────
# CONFIGURACIÓN GENERAL
# ─────────────────────────────────────────────
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "auditoria_ventas.db")
UMBRAL_DIAS = 60  # Días de gracia antes de considerar baja como penalizable

st.set_page_config(
    page_title="Portal de Gestión Comercial",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# ESTILOS CSS PERSONALIZADOS
# ─────────────────────────────────────────────
st.markdown("""
<style>
    /* ── Tipografía general ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    /* ── Ocultar elementos por defecto de Streamlit ── */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* ── Tarjetas KPI ── */
    .kpi-card {
        background: linear-gradient(135deg, #1e1e2f 0%, #2b2b44 100%);
        border-radius: 16px;
        padding: 24px 20px;
        text-align: center;
        border: 1px solid rgba(255,255,255,0.06);
        box-shadow: 0 8px 32px rgba(0,0,0,0.25);
        transition: transform 0.2s ease;
    }
    .kpi-card:hover { transform: translateY(-4px); }
    .kpi-value {
        font-size: 2.6rem;
        font-weight: 800;
        letter-spacing: -1px;
        line-height: 1.1;
    }
    .kpi-label {
        font-size: 0.82rem;
        font-weight: 500;
        color: #a0a0b8;
        margin-top: 8px;
        text-transform: uppercase;
        letter-spacing: 1.2px;
    }

    /* ── Colores semáforo ── */
    .verde  { color: #00e676; }
    .amarillo { color: #ffea00; }
    .rojo   { color: #ff5252; }
    .negro  { color: #b0bec5; }
    .total  { color: #82b1ff; }

    /* ── Badge de estado ── */
    .badge {
        display: inline-block;
        padding: 3px 12px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.5px;
    }
    .badge-verde   { background: rgba(0,230,118,0.15); color: #00e676; }
    .badge-amarillo { background: rgba(255,234,0,0.15); color: #ffea00; }
    .badge-rojo    { background: rgba(255,82,82,0.15); color: #ff5252; }
    .badge-negro   { background: rgba(176,190,197,0.15); color: #b0bec5; }

    /* ── Encabezado de sección ── */
    .section-header {
        font-size: 1.05rem;
        font-weight: 700;
        color: #e0e0e0;
        padding-bottom: 8px;
        border-bottom: 2px solid rgba(130,177,255,0.3);
        margin-bottom: 16px;
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 10px 24px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════
# 1. BASE DE DATOS — SQLite
# ═══════════════════════════════════════════════

def init_db():
    """Inicializa la base de datos SQLite y crea las tablas si no existen."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tabla de persistencia de bajas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bajas_descontadas (
            codigo_cliente TEXT PRIMARY KEY,
            fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            vendedor TEXT
        )
    """)
    
    # Tabla de reglas de semáforo
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reglas_semaforo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prioridad INTEGER,
            situacion TEXT,
            estado TEXT,
            deuda_vencida TEXT,
            en_bajas TEXT,
            tiene_fecha_bloqueo TEXT,
            descripcion TEXT
        )
    """)
    
    # Pre-carga de reglas por defecto si la tabla está vacía
    cursor.execute("SELECT COUNT(*) FROM reglas_semaforo")
    if cursor.fetchone()[0] == 0:
        reglas_default = [
            (10, "⬜ EXCLUIDO", "sin servicio", "cualquiera", "no", "no", "Nunca tuvo conexión"),
            (20, "⚫ NEGRO", "cualquiera", "cualquiera", "sí", "cualquiera", "Aparece explícitamente en Bajas"),
            (30, "🔴 ROJO", "bloqueado", "> 0", "no", "cualquiera", "Bloqueado con deuda (Prioridad sobre Negro)"),
            (40, "⚫ NEGRO", "bloqueado", "cualquiera", "cualquiera", "cualquiera", "Bloqueado general (Fallback)"),
            (50, "⚫ NEGRO", "sin servicio", "cualquiera", "cualquiera", "sí", "Sin servicio (perdió conexión)"),
            (60, "🟡 AMARILLO", "habilitado", "> 0", "no", "cualquiera", "Alerta de morosidad"),
            (70, "🟢 VERDE", "habilitado", "= 0", "no", "cualquiera", "Venta sana (Habilitado sin deuda)")
        ]
        cursor.executemany("""
            INSERT INTO reglas_semaforo 
            (prioridad, situacion, estado, deuda_vencida, en_bajas, tiene_fecha_bloqueo, descripcion)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, reglas_default)
    
    # Tabla de alias de vendedores para control de llamados
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendedores_alias (
            numero TEXT PRIMARY KEY,
            alias TEXT
        )
    """)
    
    conn.commit()
    conn.close()


def obtener_alias_db() -> dict:
    """Obtiene todos los alias de vendedores guardados en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("CREATE TABLE IF NOT EXISTS vendedores_alias (numero TEXT PRIMARY KEY, alias TEXT)")
        cursor.execute("SELECT numero, alias FROM vendedores_alias")
        rows = cursor.fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def guardar_alias_db(numero: str, alias_val: str):
    """Guarda o actualiza un alias de vendedor en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("CREATE TABLE IF NOT EXISTS vendedores_alias (numero TEXT PRIMARY KEY, alias TEXT)")
        cursor.execute("INSERT OR REPLACE INTO vendedores_alias (numero, alias) VALUES (?, ?)", (numero, alias_val))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def get_reglas() -> pd.DataFrame:
    """Obtiene las reglas ordenadas por prioridad."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM reglas_semaforo ORDER BY prioridad ASC", conn)
    conn.close()
    return df

def save_reglas(df: pd.DataFrame):
    """Reemplaza todas las reglas con el nuevo DataFrame."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reglas_semaforo")
    # Asegurar orden numérico
    df = df.sort_values(by="prioridad")
    # Evitar guardar la columna index
    df_to_save = df[["prioridad", "situacion", "estado", "deuda_vencida", "en_bajas", "tiene_fecha_bloqueo", "descripcion"]].copy()
    df_to_save.to_sql("reglas_semaforo", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()



def get_codigos_ya_descontados() -> set:
    """Retorna un set de códigos de clientes ya penalizados en el historial."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT codigo_cliente FROM bajas_descontadas", conn)
    conn.close()
    return set(df["codigo_cliente"].astype(str))


def insertar_descuentos(registros: list[dict]):
    """Inserta registros en la tabla bajas_descontadas (INSERT OR IGNORE)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for r in registros:
        cursor.execute(
            "INSERT OR IGNORE INTO bajas_descontadas (codigo_cliente, vendedor) VALUES (?, ?)",
            (str(r["codigo"]), str(r["vendedor"])),
        )
    conn.commit()
    conn.close()


def get_historial_descuentos() -> pd.DataFrame:
    """Retorna el historial completo de descuentos aplicados."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT codigo_cliente, fecha_registro, vendedor FROM bajas_descontadas ORDER BY fecha_registro DESC",
        conn,
    )
    conn.close()
    return df


# Inicializar la BD al arrancar
init_db()


# ═══════════════════════════════════════════════
# 2. PROCESAMIENTO DE DATOS
# ═══════════════════════════════════════════════

def parse_fecha(serie: pd.Series) -> pd.Series:
    """Parseo robusto de fechas con formatos mixtos (latino / anglosajón / ISO)."""
    # Intentar primero formato día/mes/año (latino)
    resultado = pd.to_datetime(serie, dayfirst=True, errors="coerce")
    # Donde falló, intentar formato mes/día/año
    mask_nat = resultado.isna()
    if mask_nat.any():
        resultado[mask_nat] = pd.to_datetime(serie[mask_nat], dayfirst=False, errors="coerce")
    return resultado


def leer_archivo(uploaded_file, **kwargs) -> pd.DataFrame:
    """Lee un archivo CSV o XLSX según su extensión."""
    nombre = uploaded_file.name.lower()
    if nombre.endswith(".xlsx") or nombre.endswith(".xls"):
        return pd.read_excel(uploaded_file, dtype=kwargs.get("dtype"), engine="openpyxl")
    else:
        return pd.read_csv(uploaded_file, dtype=kwargs.get("dtype"), sep=None, engine="python")


def procesar_datos(df_ventas: pd.DataFrame, df_db: pd.DataFrame, df_bajas: pd.DataFrame) -> pd.DataFrame:
    """
    Cruza los 3 DataFrames, aplica la lógica de semáforo y retorna
    un DataFrame unificado con la clasificación y métricas.
    """
    # ── Normalizar la columna código a string en todos los DFs ──
    for df in [df_ventas, df_db, df_bajas]:
        if "Código" in df.columns:
            df["Código"] = df["Código"].astype(str).str.strip().str.zfill(1)
        elif "Codigo" in df.columns:
            df.rename(columns={"Codigo": "Código"}, inplace=True)
            df["Código"] = df["Código"].astype(str).str.strip().str.zfill(1)

    # ── Seleccionar columnas relevantes ──
    ventas_cols = ["Código"]
    for c in ["Nombre", "Asignación", "Asignacion", "Fecha alta", "Fecha Alta", "Estado"]:
        if c in df_ventas.columns:
            ventas_cols.append(c)
    df_v = df_ventas[ventas_cols].copy()

    # Normalizar nombres de columnas de ventas
    rename_map_v = {}
    if "Asignacion" in df_v.columns and "Asignación" not in df_v.columns:
        rename_map_v["Asignacion"] = "Asignación"
    if "Fecha Alta" in df_v.columns and "Fecha alta" not in df_v.columns:
        rename_map_v["Fecha Alta"] = "Fecha alta"
    if rename_map_v:
        df_v.rename(columns=rename_map_v, inplace=True)

    # ── Merge: Ventas + DB ──
    db_cols = ["Código"]
    for c in ["Estado", "Deuda", "Deuda vencida", "Fecha de bloqueo", "Fecha bloqueo"]:
        if c in df_db.columns:
            db_cols.append(c)
    df_d = df_db[db_cols].copy()

    # Normalizar nombres de columnas de DB
    if "Fecha bloqueo" in df_d.columns and "Fecha de bloqueo" not in df_d.columns:
        df_d.rename(columns={"Fecha bloqueo": "Fecha de bloqueo"}, inplace=True)

    merged = pd.merge(df_v, df_d, on="Código", how="left", suffixes=("_ventas", "_db"))

    # Si hay dos columnas "Estado", usar la de DB como principal
    if "Estado_db" in merged.columns:
        merged["Estado"] = merged["Estado_db"]
        merged.drop(columns=["Estado_db"], inplace=True, errors="ignore")
    elif "Estado" not in merged.columns:
        merged["Estado"] = "Desconocido"

    # ── Merge: + Bajas ──
    bajas_cols = ["Código"]
    # Buscar posibles nombres de la columna de fecha del ticket y motivo
    posibles_fechas = ["Fecha", "Fecha alta", "Fecha de alta", "Fecha creacion", "Fecha creación", "Fecha del ticket"]
    posibles_motivos = ["Categoría", "Categoria", "Motivo", "Motivo de baja"]
    
    for c in posibles_fechas + posibles_motivos:
        if c in df_bajas.columns:
            bajas_cols.append(c)
            
    df_b = df_bajas[bajas_cols].copy()

    # Normalizar columna Categoría/Motivo
    for col_motivo in posibles_motivos:
        if col_motivo in df_b.columns:
            df_b.rename(columns={col_motivo: "Categoría"}, inplace=True)
            break

    # Normalizar columna de Fecha
    for col_fecha in posibles_fechas:
        if col_fecha in df_b.columns:
            df_b.rename(columns={col_fecha: "Fecha_baja"}, inplace=True)
            break

    merged = pd.merge(merged, df_b, on="Código", how="left")

    # ── Limpieza de datos ──
    # Fechas
    if "Fecha alta" in merged.columns:
        merged["Fecha alta"] = parse_fecha(merged["Fecha alta"])
    if "Fecha de bloqueo" in merged.columns:
        merged["Fecha de bloqueo"] = parse_fecha(merged["Fecha de bloqueo"])
    if "Fecha_baja" in merged.columns:
        merged["Fecha_baja"] = parse_fecha(merged["Fecha_baja"])

    # Deudas: rellenar NaN con 0
    for col in ["Deuda", "Deuda vencida"]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)
        else:
            merged[col] = 0.0

    # Estado: normalizar
    merged["Estado"] = merged["Estado"].astype(str).str.strip().str.lower()

    # Indicador de presencia en Bajas.csv
    merged["En_Bajas"] = merged["Código"].isin(df_b["Código"].unique())

    # ── Exclusión temprana: ya descontados en historial ──
    ya_descontados = get_codigos_ya_descontados()
    merged["Ya_Descontado"] = merged["Código"].isin(ya_descontados)

    # Cargar reglas desde DB
    reglas_df = get_reglas()
    # Convertir a lista de diccionarios para procesamiento ultra rápido por fila
    reglas = reglas_df.to_dict('records')

    # ── Lógica de Semáforo Dinámica ──
    def clasificar(row):
        r_estado = str(row.get("Estado", "")).lower()
        r_estado_ventas = str(row.get("Estado_ventas", "")).lower()
        r_deuda = float(row.get("Deuda vencida", 0))
        r_bajas = row.get("En_Bajas", False)
        r_bloqueo = pd.notna(row.get("Fecha de bloqueo"))

        # Pre-filtro: Excluir ventas canceladas o fallidas desde el origen
        if r_estado_ventas in ["cancelada", "fallida", "cancelado", "fallido", "rechazada", "rechazado"]:
            return "⬜ EXCLUIDO"

        for regla in reglas:
            match_estado = False
            match_deuda = False
            match_bajas = False
            match_bloqueo = False

            # Evaluar Estado
            cond_estado = str(regla["estado"]).lower()
            if cond_estado == "cualquiera" or cond_estado == r_estado:
                match_estado = True

            # Evaluar Deuda
            cond_deuda = str(regla["deuda_vencida"]).lower()
            if cond_deuda == "cualquiera":
                match_deuda = True
            elif cond_deuda == "= 0" and r_deuda == 0:
                match_deuda = True
            elif cond_deuda == "> 0" and r_deuda > 0:
                match_deuda = True

            # Evaluar Bajas
            cond_bajas = str(regla["en_bajas"]).lower()
            if cond_bajas == "cualquiera":
                match_bajas = True
            elif cond_bajas == "sí" and r_bajas:
                match_bajas = True
            elif cond_bajas == "no" and not r_bajas:
                match_bajas = True

            # Evaluar Fecha Bloqueo
            cond_bloqueo = str(regla["tiene_fecha_bloqueo"]).lower()
            if cond_bloqueo == "cualquiera":
                match_bloqueo = True
            elif cond_bloqueo == "sí" and r_bloqueo:
                match_bloqueo = True
            elif cond_bloqueo == "no" and not r_bloqueo:
                match_bloqueo = True

            # Si todas las condiciones matchean, retornar situación
            if match_estado and match_deuda and match_bajas and match_bloqueo:
                return regla["situacion"]

        return "🟡 AMARILLO"  # Default fallback absoluto


    merged["Semáforo"] = merged.apply(clasificar, axis=1)

    # ── Cálculo de Días Activos ──
    def calcular_dias_activos(row):
        fecha_alta = row.get("Fecha alta")
        fecha_fin = row.get("Fecha de bloqueo")
        fecha_baja = row.get("Fecha_baja")

        if pd.isna(fecha_alta):
            return None

        # Priorizar fecha de baja si existe, luego fecha de bloqueo
        if pd.notna(fecha_baja):
            ref = fecha_baja
        elif pd.notna(fecha_fin):
            ref = fecha_fin
        else:
            ref = pd.Timestamp(datetime.now())

        dias = (ref - fecha_alta).days
        return max(dias, 0)

    merged["Días Activos"] = merged.apply(calcular_dias_activos, axis=1)

    # ── Regla de Penalización ──
    def evaluar_penalizacion(row):
        if row["Ya_Descontado"]:
            return "✅ Ya descontado históricamente"
        if row["Semáforo"] != "⚫ NEGRO":
            return "-"
        dias = row.get("Días Activos")
        if pd.isna(dias):
            return "-"
        if dias <= UMBRAL_DIAS:
            return f"🚨 PENALIZABLE ({dias} días)"
        else:
            return f"Baja natural ({dias} días)"

    merged["Penalización"] = merged.apply(evaluar_penalizacion, axis=1)

    # Motivo de baja y Fecha de baja
    if "Categoría" in merged.columns:
        merged["Motivo Baja"] = merged["Categoría"].fillna("-")
    else:
        merged["Motivo Baja"] = "-"
        
    if "Fecha_baja" in merged.columns:
        merged["Fecha de baja"] = merged["Fecha_baja"].dt.strftime("%d/%m/%Y").fillna("-")
    else:
        merged["Fecha de baja"] = "-"

    # Vendedor
    if "Asignación" not in merged.columns:
        merged["Asignación"] = "Sin asignar"
    merged["Asignación"] = merged["Asignación"].fillna("Sin asignar")

    # Nombre
    if "Nombre" not in merged.columns:
        merged["Nombre"] = "-"
    merged["Nombre"] = merged["Nombre"].fillna("-")

    return merged


# ═══════════════════════════════════════════════
# 3. GENERADOR DE PDF CORPORATIVO (FPDF2)
# ═══════════════════════════════════════════════

class PDFInforme(FPDF):
    """PDF con estilo corporativo para informes de auditoría."""

    COLOR_PRIMARY = (30, 30, 47)       # Azul oscuro corporativo
    COLOR_ACCENT = (82, 130, 255)      # Azul acento
    COLOR_HEADER_BG = (40, 40, 62)     # Fondo encabezado tabla
    COLOR_ROW_ALT = (245, 245, 250)    # Fila alternada
    COLOR_TEXT = (50, 50, 50)          # Texto principal
    COLOR_MUTED = (130, 130, 150)      # Texto secundario
    COLOR_WHITE = (255, 255, 255)

    SEMAFORO_COLORS = {
        "🟢 VERDE": (0, 200, 83),
        "🟡 AMARILLO": (255, 193, 7),
        "🔴 ROJO": (244, 67, 54),
        "⚫ NEGRO": (96, 96, 96),
    }

    def __init__(self, vendedor: str, fecha_reporte: str):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.vendedor = vendedor
        self.fecha_reporte = fecha_reporte
        self.set_auto_page_break(auto=True, margin=30)

    def header(self):
        # ── Barra superior degradada ──
        self.set_fill_color(*self.COLOR_PRIMARY)
        self.rect(0, 0, self.w, 32, "F")
        # Línea de acento
        self.set_fill_color(*self.COLOR_ACCENT)
        self.rect(0, 32, self.w, 1.5, "F")

        # Título principal
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*self.COLOR_WHITE)
        self.set_xy(12, 7)
        self.cell(0, 8, "INFORME DE AUDITORÍA COMERCIAL", new_x="LMARGIN", new_y="NEXT")

        # Subtítulo
        self.set_font("Helvetica", "", 9)
        self.set_text_color(180, 190, 255)
        self.set_xy(12, 16)
        self.cell(0, 6, "Control de Retención 60 Días", new_x="LMARGIN", new_y="NEXT")

        # Vendedor y fecha en la derecha
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*self.COLOR_WHITE)
        self.set_xy(self.w - 120, 8)
        self.cell(108, 6, f"Vendedor: {self.vendedor}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(180, 190, 255)
        self.set_xy(self.w - 120, 16)
        self.cell(108, 6, f"Fecha: {self.fecha_reporte}", align="R", new_x="LMARGIN", new_y="NEXT")

        self.ln(22)

    def footer(self):
        self.set_y(-25)
        # Línea separadora
        self.set_draw_color(*self.COLOR_ACCENT)
        self.set_line_width(0.3)
        self.line(12, self.h - 25, self.w - 12, self.h - 25)

        # Nota legal
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*self.COLOR_MUTED)
        self.set_xy(12, self.h - 23)
        self.multi_cell(
            self.w - 24, 3.5,
            "NOTA LEGAL: El vendedor dispone de cinco (5) días hábiles a partir de la recepción "
            "de este informe para presentar descargos o apelaciones formales ante el área de "
            "Auditoría Comercial. Transcurrido dicho plazo sin respuesta, las penalizaciones "
            "se considerarán firmes y se aplicarán al siguiente ciclo de liquidación.",
            align="L",
        )

        # Número de página
        self.set_font("Helvetica", "", 7)
        self.set_xy(self.w - 40, self.h - 10)
        self.cell(28, 4, f"Página {self.page_no()}/{{nb}}", align="R")

    def agregar_tarjetas_resumen(self, total: int, verdes: int, alertas: int, penalizables: int):
        """Agrega tarjetas de resumen numérico al PDF."""
        y_start = self.get_y()
        card_w = 60
        card_h = 22
        gap = 8
        x_start = (self.w - (card_w * 4 + gap * 3)) / 2

        items = [
            ("Ventas Totales", str(total), self.COLOR_ACCENT),
            ("Validadas (Verde)", str(verdes), (0, 200, 83)),
            ("En Alerta", str(alertas), (255, 193, 7)),
            ("Penalizables", str(penalizables), (244, 67, 54)),
        ]

        for i, (label, value, color) in enumerate(items):
            x = x_start + i * (card_w + gap)
            # Fondo de la tarjeta
            self.set_fill_color(248, 248, 252)
            self.set_draw_color(220, 220, 230)
            self.rect(x, y_start, card_w, card_h, "FD")
            # Barra superior de color
            self.set_fill_color(*color)
            self.rect(x, y_start, card_w, 2.5, "F")
            # Valor
            self.set_font("Helvetica", "B", 16)
            self.set_text_color(*self.COLOR_TEXT)
            self.set_xy(x, y_start + 4)
            self.cell(card_w, 8, value, align="C", new_x="LMARGIN", new_y="NEXT")
            # Label
            self.set_font("Helvetica", "", 7)
            self.set_text_color(*self.COLOR_MUTED)
            self.set_xy(x, y_start + 13)
            self.cell(card_w, 5, label, align="C", new_x="LMARGIN", new_y="NEXT")

        self.set_y(y_start + card_h + 8)

    def agregar_tabla_detalle(self, df: pd.DataFrame):
        """Genera la tabla de detalle de clientes."""
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*self.COLOR_TEXT)
        self.cell(0, 8, "DETALLE DE CLIENTES EN ALERTA Y BAJA", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

        # Definir columnas y anchos
        columnas = ["Código", "Cliente", "F. Alta", "Días Act.", "Estado", "Semáforo", "Motivo", "F. Baja"]
        anchos = [18, 50, 20, 16, 25, 25, 25, 18]

        # Encabezado de la tabla
        self.set_fill_color(*self.COLOR_HEADER_BG)
        self.set_text_color(*self.COLOR_WHITE)
        self.set_font("Helvetica", "B", 7.5)
        row_h = 7

        for i, col in enumerate(columnas):
            self.cell(anchos[i], row_h, col, border=0, align="C", fill=True)
        self.ln(row_h)

        # Filas de datos
        self.set_font("Helvetica", "", 7)
        alt = False

        for _, row in df.iterrows():
            # Alternar color de fondo
            if alt:
                self.set_fill_color(*self.COLOR_ROW_ALT)
            else:
                self.set_fill_color(255, 255, 255)
            alt = not alt

            # Verificar salto de pagina
            if self.get_y() + row_h > self.h - 30:
                self.add_page()
                # Re-dibujar encabezado de tabla
                self.set_fill_color(*self.COLOR_HEADER_BG)
                self.set_text_color(*self.COLOR_WHITE)
                self.set_font("Helvetica", "B", 7.5)
                for i, col in enumerate(columnas):
                    self.cell(anchos[i], row_h, col, border=0, align="C", fill=True)
                self.ln(row_h)
                self.set_font("Helvetica", "", 7)
                alt = False

            # Preparar datos de la fila
            codigo = str(row.get("Código", ""))[:12]
            nombre = str(row.get("Nombre", ""))[:35]
            f_alta = ""
            if pd.notna(row.get("Fecha alta")):
                try:
                    f_alta = row["Fecha alta"].strftime("%d/%m/%Y")
                except Exception:
                    f_alta = str(row.get("Fecha alta", ""))[:10]
            dias = str(int(row["Días Activos"])) if pd.notna(row.get("Días Activos")) else "-"
            estado = str(row.get("Estado", ""))[:15].title()
            semaforo_raw = str(row.get("Semáforo", ""))
            motivo = str(row.get("Motivo Baja", "-"))[:25]
            f_baja = str(row.get("Fecha de baja", "-"))

            # Color del semáforo
            semaforo_text = semaforo_raw.split(" ")[-1] if " " in semaforo_raw else semaforo_raw
            sem_color = self.SEMAFORO_COLORS.get(semaforo_raw, self.COLOR_TEXT)

            self.set_text_color(*self.COLOR_TEXT)
            self.cell(anchos[0], row_h, codigo, border=0, align="C", fill=True)
            self.cell(anchos[1], row_h, nombre, border=0, align="L", fill=True)
            self.cell(anchos[2], row_h, f_alta, border=0, align="C", fill=True)
            self.cell(anchos[3], row_h, dias, border=0, align="C", fill=True)
            self.cell(anchos[4], row_h, estado, border=0, align="C", fill=True)

            # Celda semáforo con color
            self.set_text_color(*sem_color)
            self.set_font("Helvetica", "B", 7)
            self.cell(anchos[5], row_h, semaforo_text, border=0, align="C", fill=True)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(*self.COLOR_TEXT)

            self.cell(anchos[6], row_h, motivo, border=0, align="L", fill=True)
            self.ln(row_h)

        # Línea final
        self.set_draw_color(200, 200, 210)
        self.line(12, self.get_y(), self.w - 12, self.get_y())

    def agregar_firmas(self):
        """Agrega la sección de firmas al final del informe."""
        y = self.get_y() + 15
        if y > self.h - 50:
            self.add_page()
            y = self.get_y() + 10

        self.set_y(y)
        line_w = 70
        gap = 40
        x_firma1 = (self.w / 2) - line_w - (gap / 2)
        x_firma2 = (self.w / 2) + (gap / 2)

        self.set_draw_color(*self.COLOR_TEXT)
        self.set_line_width(0.4)

        # Firma Auditor
        self.line(x_firma1, y + 20, x_firma1 + line_w, y + 20)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*self.COLOR_TEXT)
        self.set_xy(x_firma1, y + 22)
        self.cell(line_w, 5, "Firma del Auditor", align="C")
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*self.COLOR_MUTED)
        self.set_xy(x_firma1, y + 27)
        self.cell(line_w, 4, "Departamento de Auditoría Comercial", align="C")

        # Firma Vendedor
        self.line(x_firma2, y + 20, x_firma2 + line_w, y + 20)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*self.COLOR_TEXT)
        self.set_xy(x_firma2, y + 22)
        self.cell(line_w, 5, "Firma del Vendedor", align="C")
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*self.COLOR_MUTED)
        self.set_xy(x_firma2, y + 27)
        self.cell(line_w, 4, self.vendedor, align="C")


def generar_pdf(vendedor: str, df_vendedor: pd.DataFrame, metricas: dict) -> bytes:
    """Genera el PDF del informe y retorna los bytes."""
    fecha_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    pdf = PDFInforme(vendedor=vendedor, fecha_reporte=fecha_str)
    pdf.alias_nb_pages()
    pdf.add_page()

    # Tarjetas resumen
    pdf.agregar_tarjetas_resumen(
        total=metricas["total"],
        verdes=metricas["verdes"],
        alertas=metricas["alertas"],
        penalizables=metricas["penalizables"],
    )

    pdf.ln(4)

    # Tabla de detalle (solo amarillo, rojo, negro) y ordenamiento por severidad y deuda
    semaforo_orden = {"⚫ NEGRO": 1, "🔴 ROJO": 2, "🟡 AMARILLO": 3}
    df_detalle = df_vendedor[
        df_vendedor["Semáforo"].isin(semaforo_orden.keys())
    ].copy()
    df_detalle["_Orden"] = df_detalle["Semáforo"].map(semaforo_orden)
    df_detalle = df_detalle.sort_values(by=["_Orden", "Deuda vencida"], ascending=[True, False])

    if len(df_detalle) > 0:
        pdf.agregar_tabla_detalle(df_detalle)
    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 10, "No se encontraron clientes en alerta o baja para este vendedor.", align="C")

    # Firmas
    pdf.agregar_firmas()

    return pdf.output()


class PDFInformeLlamados(FPDF):
    """PDF con estilo corporativo en formato horizontal para informes de control de llamados."""

    COLOR_BG = (18, 18, 30)            # Fondo de página oscuro
    COLOR_PRIMARY = (30, 30, 47)       # Fondo de tarjetas / cabecera oscuro
    COLOR_ACCENT = (82, 130, 255)      # Azul acento
    COLOR_HEADER_BG = (40, 40, 62)     # Fondo encabezado
    COLOR_ROW_ALT = (45, 45, 68)       # Fila alternada (si se usa)
    COLOR_TEXT = (240, 240, 250)       # Texto principal claro
    COLOR_MUTED = (160, 160, 180)      # Texto secundario claro
    COLOR_WHITE = (255, 255, 255)

    def __init__(self, vendedor: str, fecha_reporte: str, filtro_estado: str):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.vendedor = vendedor
        self.fecha_reporte = fecha_reporte
        self.filtro_estado = filtro_estado
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        # Dibujar fondo oscuro en toda la página
        self.set_fill_color(*self.COLOR_BG)
        self.rect(0, 0, self.w, self.h, "F")

        # ── Barra superior ──
        self.set_fill_color(*self.COLOR_PRIMARY)
        self.rect(0, 0, self.w, 32, "F")
        # Línea de acento
        self.set_fill_color(*self.COLOR_ACCENT)
        self.rect(0, 32, self.w, 1.5, "F")

        # Título principal
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*self.COLOR_WHITE)
        self.set_xy(12, 7)
        self.cell(0, 8, "PORTAL DE GESTIÓN - CONTROL DE LLAMADOS", new_x="LMARGIN", new_y="NEXT")

        # Subtítulo
        self.set_font("Helvetica", "", 9)
        self.set_text_color(180, 190, 255)
        self.set_xy(12, 16)
        self.cell(0, 6, f"Filtro Respuesta: {self.filtro_estado}", new_x="LMARGIN", new_y="NEXT")

        # Vendedor y fecha en la derecha
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*self.COLOR_WHITE)
        self.set_xy(self.w - 120, 8)
        self.cell(108, 6, f"Vendedor: {self.vendedor}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(180, 190, 255)
        self.set_xy(self.w - 120, 16)
        self.cell(108, 6, f"Fecha: {self.fecha_reporte}", align="R", new_x="LMARGIN", new_y="NEXT")

        self.ln(22)

    def footer(self):
        self.set_y(-15)
        # Línea separadora
        self.set_draw_color(*self.COLOR_ACCENT)
        self.set_line_width(0.3)
        self.line(12, self.h - 15, self.w - 12, self.h - 15)

        # Nota
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(180, 190, 255)
        self.set_xy(12, self.h - 13)
        self.cell(150, 4, "Portal de Gestión Comercial - Todos los derechos reservados.")

        # Número de página
        self.set_font("Helvetica", "", 7)
        self.set_xy(self.w - 40, self.h - 13)
        self.cell(28, 4, f"Página {self.page_no()}/{{nb}}", align="R")

    def agregar_tarjetas_resumen(self, total: int, nros_distintos: int, contestadas: int, pct_efectividad: float, duracion_prom: str, fuera_horario: int, intentos_lead: float):
        """Agrega las 7 tarjetas de KPI al PDF en formato horizontal."""
        y_start = self.get_y()
        card_w = 34
        card_h = 22
        gap = 5
        x_start = (self.w - (card_w * 7 + gap * 6)) / 2

        items = [
            ("Total Llamadas", f"{total:,}", self.COLOR_ACCENT),
            ("Nros Distintos", f"{nros_distintos:,}", self.COLOR_ACCENT),
            ("Contestadas", f"{contestadas:,}", (0, 230, 118)),
            ("Efectividad", f"{pct_efectividad:.1f}%", (255, 234, 0)),
            ("Duración Prom.", duracion_prom, self.COLOR_ACCENT),
            ("Fuera Horario", f"{fuera_horario:,}", (255, 82, 82)),
            ("Intentos x Nro", f"{intentos_lead:.2f}", (179, 136, 255)),
        ]

        for i, (label, value, color) in enumerate(items):
            x = x_start + i * (card_w + gap)
            # Fondo de la tarjeta
            self.set_fill_color(*self.COLOR_PRIMARY)
            self.set_draw_color(45, 45, 68)
            self.rect(x, y_start, card_w, card_h, "FD")
            # Barra superior de color
            self.set_fill_color(*color)
            self.rect(x, y_start, card_w, 2.5, "F")
            # Valor
            self.set_font("Helvetica", "B", 13)
            self.set_text_color(*self.COLOR_WHITE)
            self.set_xy(x, y_start + 4)
            self.cell(card_w, 8, value, align="C", new_x="LMARGIN", new_y="NEXT")
            # Label
            self.set_font("Helvetica", "", 7)
            self.set_text_color(*self.COLOR_MUTED)
            self.set_xy(x, y_start + 13)
            self.cell(card_w, 5, label, align="C", new_x="LMARGIN", new_y="NEXT")

        self.set_y(y_start + card_h + 8)

    def agregar_graficos(self, donut_path: str, diario_path: str):
        """Agrega los gráficos de Donut y diario lado a lado en la página actual."""
        y = self.get_y()
        chart_w = 132
        chart_h = 75
        x_gap = 10
        x_start = (self.w - (chart_w * 2 + x_gap)) / 2

        if os.path.exists(donut_path) and donut_path:
            self.image(donut_path, x=x_start, y=y, w=chart_w, h=chart_h)
        if os.path.exists(diario_path) and diario_path:
            self.image(diario_path, x=x_start + chart_w + x_gap, y=y, w=chart_w, h=chart_h)

        self.set_y(y + chart_h + 5)

    def agregar_mapas_calor_p1(self, franja_path: str, semanal_path: str):
        """Agrega los heatmaps de franja horaria y semanal en la página 2."""
        y = self.get_y()
        heatmap_w = 270
        heatmap_h = 58
        x = (self.w - heatmap_w) / 2

        if os.path.exists(franja_path) and franja_path:
            self.image(franja_path, x=x, y=y, w=heatmap_w, h=heatmap_h)
            y += heatmap_h + 5

        if os.path.exists(semanal_path) and semanal_path:
            self.image(semanal_path, x=x, y=y, w=heatmap_w, h=heatmap_h)

        self.set_y(y + heatmap_h + 5)

    def agregar_mapa_mensual_y_firmas(self, mensual_path: str):
        """Agrega el heatmap mensual y las firmas en la página 3."""
        y = self.get_y()
        heatmap_w = 270
        heatmap_h = 58
        x = (self.w - heatmap_w) / 2

        if os.path.exists(mensual_path) and mensual_path:
            self.image(mensual_path, x=x, y=y, w=heatmap_w, h=heatmap_h)
            y += heatmap_h + 5

        self.set_y(y)
        self.agregar_firmas()

    def agregar_firmas(self):
        """Agrega la sección de firmas al final del informe en colores claros."""
        y = self.get_y() + 8
        if y > self.h - 45:
            self.add_page()
            y = self.get_y() + 5

        self.set_y(y)
        line_w = 70
        gap = 40
        x_firma1 = (self.w / 2) - line_w - (gap / 2)
        x_firma2 = (self.w / 2) + (gap / 2)

        self.set_draw_color(*self.COLOR_MUTED)
        self.set_line_width(0.4)

        # Firma Auditor
        self.line(x_firma1, y + 15, x_firma1 + line_w, y + 15)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*self.COLOR_WHITE)
        self.set_xy(x_firma1, y + 17)
        self.cell(line_w, 5, "Firma del Auditor", align="C")
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*self.COLOR_MUTED)
        self.set_xy(x_firma1, y + 22)
        self.cell(line_w, 4, "Departamento de Auditoría Comercial", align="C")

        # Firma Vendedor
        self.line(x_firma2, y + 15, x_firma2 + line_w, y + 15)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*self.COLOR_WHITE)
        self.set_xy(x_firma2, y + 17)
        self.cell(line_w, 5, "Firma del Vendedor", align="C")
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*self.COLOR_MUTED)
        self.set_xy(x_firma2, y + 22)
        self.cell(line_w, 4, self.vendedor, align="C")


def generar_pdf_llamados(vendedor: str, metricas: dict, filtro_estado: str,
                         fig_pie, fig_diario, fig_heat, fig_heat_sem, fig_heat_mensual) -> bytes:
    """Genera el PDF del informe de llamados en formato horizontal y retorna los bytes."""
    fecha_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    pdf = PDFInformeLlamados(vendedor=vendedor, fecha_reporte=fecha_str, filtro_estado=filtro_estado)
    pdf.alias_nb_pages()
    
    # Crear archivos temporales para los gráficos
    temp_files = []
    
    def save_fig_temp(fig, width=700, height=400):
        if fig is None:
            return ""
        # Configurar la figura para el PDF (fondo oscuro para que combine con el diseño)
        fig_copy = go.Figure(fig)
        fig_copy.update_layout(
            paper_bgcolor="#1e1e2f",
            plot_bgcolor="#1e1e2f",
            font=dict(color="#e0e0e0"),
        )
        if hasattr(fig_copy, "layout"):
            if fig_copy.layout.xaxis:
                fig_copy.update_xaxes(gridcolor="rgba(255,255,255,0.05)", showgrid=True)
            if fig_copy.layout.yaxis:
                fig_copy.update_yaxes(gridcolor="rgba(255,255,255,0.05)", showgrid=True)
        
        # Escribir a archivo temporal
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            fig_copy.write_image(path, format="png", width=width, height=height, scale=2)
            temp_files.append(path)
            return path
        except Exception as e:
            if os.path.exists(path):
                os.remove(path)
            # Imprimir en consola/consola de logs de streamlit para depurar
            print(f"Error al exportar gráfico a imagen: {e}")
            return ""

    # Guardar cada figura
    donut_path = save_fig_temp(fig_pie, width=600, height=400)
    diario_path = save_fig_temp(fig_diario, width=800, height=400)
    franja_path = save_fig_temp(fig_heat, width=1200, height=350)
    semanal_path = save_fig_temp(fig_heat_sem, width=1200, height=350)
    mensual_path = save_fig_temp(fig_heat_mensual, width=1200, height=350)

    # PÁGINA 1: KPIs + Gráficos Donut y Diario
    pdf.add_page()
    pdf.agregar_tarjetas_resumen(
        total=metricas["total"],
        nros_distintos=metricas["nros_distintos"],
        contestadas=metricas["contestadas"],
        pct_efectividad=metricas["pct_efectividad"],
        duracion_prom=metricas["duracion_prom"],
        fuera_horario=metricas["fuera_horario"],
        intentos_lead=metricas["intentos_lead"]
    )
    pdf.agregar_graficos(donut_path, diario_path)

    # PÁGINA 2: Heatmaps de franja horaria y semanal
    pdf.add_page()
    pdf.agregar_mapas_calor_p1(franja_path, semanal_path)

    # PÁGINA 3: Heatmap mensual + Firmas
    pdf.add_page()
    pdf.agregar_mapa_mensual_y_firmas(mensual_path)

    try:
        pdf_out = pdf.output()
    finally:
        for path in temp_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    return bytes(pdf_out)


# ═══════════════════════════════════════════════
# 4. INTERFAZ DE USUARIO (STREAMLIT)
# ═══════════════════════════════════════════════

def render_kpi_card(value, label, color_class):
    """Renderiza una tarjeta KPI con HTML/CSS."""
    return f"""
    <div class="kpi-card">
        <div class="kpi-value {color_class}">{value}</div>
        <div class="kpi-label">{label}</div>
    </div>
    """



# ═══════════════════════════════════════════════
# 5. MÓDULO DE CONTROL DE LLAMADOS
# ═══════════════════════════════════════════════

# Columnas clave del CDR
COL_VENDEDOR     = "Nombre del llamante"
COL_NUMERO       = "Número del llamante"
COL_ESTADO       = "Estado de la llamada"
COL_DURACION     = "Duración de la llamada"
COL_CONVERSACION = "Tiempo de conversación"
COL_INICIO       = "Tiempo de inicio"
COL_FIN          = "Hora de finalización"
COL_TIPO         = "Tipo de llamada"
COL_DESTINO      = "Número de destinatario"
COL_RESPONDIDA   = "Respondida por"
COL_ACCION       = "Tipo de acción"

HORA_LABORAL_INICIO = 9
HORA_LABORAL_FIN = 18


def modulo_llamados():
    """Módulo completo de Control de Llamados con análisis por franja horaria."""

    st.markdown("""
    <div style="text-align:center; padding: 10px 0 25px 0;">
        <h1 style="margin:0; font-size:2.2rem; font-weight:800;
                   background: linear-gradient(135deg, #82b1ff, #b388ff);
                   -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
            📞 Panel de Control de Llamados
        </h1>
        <p style="color:#a0a0b8; font-size:0.95rem; margin-top:4px;">
            Análisis de productividad telefónica · Franjas horarias · Efectividad por vendedor
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Carga de archivo ──
    st.markdown('<div class="section-header">📂 Carga de Informe CDR</div>', unsafe_allow_html=True)
    file_llamadas = st.file_uploader(
        "Arrastrá o seleccioná el archivo de llamadas (CSV / XLSX)",
        type=["csv", "xlsx", "xls"],
        key="llamados_file_uploader",
    )

    if file_llamadas is None:
        st.info("👆 Cargá el archivo de informe de llamadas para comenzar el análisis.")
        return

    # Leer archivo
    df_calls = leer_archivo(file_llamadas)
    if df_calls is None or len(df_calls) == 0:
        st.error("No se pudieron leer datos del archivo.")
        return

    # ── Validar columnas mínimas ──
    columnas_requeridas = [COL_NUMERO, COL_ESTADO, COL_DURACION, COL_INICIO]
    cols_faltantes = [c for c in columnas_requeridas if c not in df_calls.columns]
    if cols_faltantes:
        st.error(f"Faltan columnas requeridas en el archivo: **{', '.join(cols_faltantes)}**")
        st.markdown("Columnas encontradas: " + ", ".join(df_calls.columns.tolist()))
        return

    # ── Procesamiento ──
    with st.spinner("🔄 Procesando datos de llamadas..."):
        df = df_calls.copy()

        # Filtrar solo registros principales (excluir sub_cdr que son copias)
        if "CDR" in df.columns:
            total_antes = len(df)
            df = df[df["CDR"].astype(str).str.lower().str.contains("main", na=False)].copy()
            descartados = total_antes - len(df)
            if descartados > 0:
                st.info(f"Se descartaron **{descartados:,}** registros sub_cdr (copias). Se analizan **{len(df):,}** registros main_cdr.")

        # Parsear fechas
        df[COL_INICIO] = pd.to_datetime(df[COL_INICIO], errors="coerce")

        # Parsear duraciones numéricas
        for col_dur in [COL_DURACION, COL_CONVERSACION]:
            if col_dur in df.columns:
                df[col_dur] = pd.to_numeric(df[col_dur], errors="coerce").fillna(0)

        # Extraer hora y fecha
        df["_Hora"] = df[COL_INICIO].dt.hour
        df["_Fecha"] = df[COL_INICIO].dt.date

        # Clasificar estado
        df["_Contestada"] = df[COL_ESTADO].str.upper().str.contains("ANSWERED|CONTESTADA", na=False)

        # Clasificar si cae dentro del horario laboral
        df["_En_Horario"] = df["_Hora"].between(HORA_LABORAL_INICIO, HORA_LABORAL_FIN - 1)

        # Asegurar que el número del llamante sea string
        if COL_NUMERO in df.columns:
            df[COL_NUMERO] = df[COL_NUMERO].astype(str).str.strip()

    st.success(f"\u2705 Se procesaron **{len(df):,}** registros de llamadas.")
    st.markdown("---")

    # ══════════════════════════════════════
    # SISTEMA DE ALIAS (Número → Vendedor)
    # ══════════════════════════════════════
    col_id = COL_NUMERO if COL_NUMERO in df.columns else COL_VENDEDOR

    numeros_unicos = sorted(df[col_id].dropna().unique().tolist())

    # Inicializar alias en session_state desde la base de datos
    if "llamados_alias" not in st.session_state:
        st.session_state["llamados_alias"] = obtener_alias_db()

    # Pre-cargar alias con el nombre del llamante si existe y no está en session_state ni en BD
    alias = st.session_state["llamados_alias"]
    alias_db = obtener_alias_db()
    
    if COL_VENDEDOR in df.columns and COL_NUMERO in df.columns:
        for num in numeros_unicos:
            if num not in alias:
                if num in alias_db:
                    alias[num] = alias_db[num]
                else:
                    nombres = df.loc[df[col_id] == num, COL_VENDEDOR].dropna().unique()
                    alias[num] = nombres[0] if len(nombres) > 0 else num
                    # Guardar por defecto en la base de datos para futuras sesiones
                    guardar_alias_db(num, alias[num])

    with st.expander("\u270f\ufe0f Configurar Alias de Vendedores (Número → Nombre)", expanded=False):
        st.markdown("Asigná un nombre legible a cada número de extensión/teléfono.")
        cols_alias = st.columns(2)
        alias_actualizado = {}
        for i, num in enumerate(numeros_unicos):
            with cols_alias[i % 2]:
                nuevo_val = st.text_input(
                    f"📞 {num}",
                    value=alias.get(num, num),
                    key=f"alias_{num}",
                )
                alias_actualizado[num] = nuevo_val
                # Si el valor cambió en comparación con lo que teníamos en memoria, guardamos en la base de datos
                if nuevo_val != alias.get(num, None):
                    guardar_alias_db(num, nuevo_val)
                    
        st.session_state["llamados_alias"] = alias_actualizado

    # Aplicar alias al DataFrame
    df["_Vendedor"] = df[col_id].map(alias_actualizado).fillna(df[col_id]).astype(str)

    # ── Filtros del Dashboard ──
    vendedores_display = sorted(df["_Vendedor"].dropna().unique().tolist())
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        filtro_vendedor = st.selectbox(
            "Filtrar por vendedor:",
            options=["Todos"] + vendedores_display,
            index=0,
            key="llamados_filtro_vendedor",
        )
    with col_f2:
        filtro_tipo = st.radio(
            "Estado de respuesta (Filtra todo el dashboard):",
            options=["Todas", "Contestadas", "No Contestadas"],
            index=0,
            horizontal=True,
            key="llamados_filtro_tipo",
        )

    # Aplicar filtros
    if filtro_vendedor != "Todos":
        df_display = df[df["_Vendedor"] == filtro_vendedor].copy()
    else:
        df_display = df.copy()

    if filtro_tipo == "Contestadas":
        df_display = df_display[df_display["_Contestada"]].copy()
    elif filtro_tipo == "No Contestadas":
        df_display = df_display[~df_display["_Contestada"]].copy()

    # Inicializar el filtro de KPI en session_state si no existe
    if "llamados_filtro_kpi" not in st.session_state:
        st.session_state["llamados_filtro_kpi"] = "Todas"

    dias_abrev = {0: "L", 1: "M", 2: "Mi", 3: "J", 4: "V", 5: "S", 6: "D"}

    # ════════════════════════════════════════
    # KPIs y Cálculos
    # ════════════════════════════════════════
    total = len(df_display)
    nros_distintos = df_display[COL_DESTINO].nunique() if COL_DESTINO in df_display.columns else 0
    contestadas = df_display["_Contestada"].sum()
    pct_efectividad = (contestadas / total * 100) if total > 0 else 0

    dur_conv = 0
    if COL_CONVERSACION in df_display.columns:
        dur_conv = df_display.loc[df_display["_Contestada"], COL_CONVERSACION].mean()
    if pd.isna(dur_conv):
        dur_conv = 0
    dur_min = int(dur_conv) // 60
    dur_seg = int(dur_conv) % 60

    en_horario = df_display["_En_Horario"].sum()
    fuera_horario = total - en_horario

    intentos_lead = (total / nros_distintos) if nros_distintos > 0 else 0

    # ── Filtrar la tabla de detalle según el KPI seleccionado ──
    filtro_kpi = st.session_state.get("llamados_filtro_kpi", "Todas")
    df_detalle = df_display.copy()

    if filtro_kpi == "Contestadas":
        df_detalle = df_detalle[df_detalle["_Contestada"]]
    elif filtro_kpi == "No Contestadas":
        df_detalle = df_detalle[~df_detalle["_Contestada"]]
    elif filtro_kpi == "Fuera de Horario":
        df_detalle = df_detalle[~df_detalle["_En_Horario"]]
    elif filtro_kpi == "Nros Distintos":
        df_detalle = df_detalle.drop_duplicates(subset=[COL_DESTINO])

    # ════════════════════════════════════════
    # PRE-GENERAR GRÁFICOS Y MAPAS DE CALOR (para PDF y UI)
    # ════════════════════════════════════════
    color_estado = {
        "ANSWERED": "#00e676",
        "NO ANSWER": "#ff5252",
        "BUSY": "#ffea00",
        "FAILED": "#b0bec5",
    }

    # 1. Gráfico de dona de estados
    sem_counts = df_display[COL_ESTADO].value_counts().reset_index()
    sem_counts.columns = ["Estado", "Cantidad"]
    fig_pie = px.pie(
        sem_counts,
        values="Cantidad",
        names="Estado",
        color="Estado",
        color_discrete_map=color_estado,
        hole=0.45,
    )
    fig_pie.update_layout(
        title=dict(text="Distribución por Estado de Llamada", font=dict(size=14)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0", family="Inter"),
        legend=dict(font=dict(size=10)),
        height=380,
        margin=dict(t=50, b=20, l=20, r=20),
    )

    # 2. Gráfico de barras apiladas diarias
    fig_diario = None
    if len(df_display) > 0 and df_display["_Fecha"].notna().any():
        df_display["_Fecha_Formateada"] = df_display["_Fecha"].apply(
            lambda d: f"{dias_abrev[d.weekday()]} {d.day:02d}/{d.month:02d}" if not pd.isna(d) else "Sin Fecha"
        )
        
        df_diario_grp = df_display.groupby(["_Fecha", "_Fecha_Formateada", "_Vendedor"]).size().reset_index(name="Llamadas")
        df_diario_grp = df_diario_grp.sort_values("_Fecha")
        
        fig_diario = px.bar(
            df_diario_grp,
            x="_Fecha_Formateada",
            y="Llamadas",
            color="_Vendedor",
            barmode="stack",
            color_discrete_sequence=px.colors.qualitative.Pastel + px.colors.qualitative.Bold,
        )
        fig_diario.update_layout(
            title=dict(text="Distribución Diaria de Llamadas (Acumulado por Vendedor)", font=dict(size=14)),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e0e0e0", family="Inter"),
            xaxis=dict(title="", tickangle=-45, type="category"),
            yaxis=dict(title="Llamadas", gridcolor="rgba(255,255,255,0.05)"),
            legend=dict(title="Vendedor", font=dict(size=9)),
            height=380,
            margin=dict(t=50, b=80, l=40, r=20),
        )

    # 3. Heatmap 1: Franja Horaria (30 min)
    df_horas = df_display[df_display["_Hora"].between(6, 21)].copy()
    def asignar_ventana_30(row):
        if pd.isna(row[COL_INICIO]):
            return "06:00-06:30"
        h = row[COL_INICIO].hour
        m = row[COL_INICIO].minute
        if m < 30:
            return f"{h:02d}:00-{h:02d}:30"
        else:
            return f"{h:02d}:30-{h+1:02d}:00"

    df_horas["_Ventana"] = df_horas.apply(asignar_ventana_30, axis=1)

    ventanas_orden = []
    for h in range(6, 22):
        ventanas_orden.append(f"{h:02d}:00-{h:02d}:30")
        ventanas_orden.append(f"{h:02d}:30-{h+1:02d}:00")

    heatmap_data = df_horas.groupby(["_Vendedor", "_Ventana"]).size().reset_index(name="Llamadas")
    heatmap_data["_Ventana"] = pd.Categorical(heatmap_data["_Ventana"], categories=ventanas_orden, ordered=True)

    pivot = heatmap_data.pivot_table(
        index="_Vendedor",
        columns="_Ventana",
        values="Llamadas",
        aggfunc="sum",
        fill_value=0,
    )

    for v in ventanas_orden:
        if v not in pivot.columns:
            pivot[v] = 0
    pivot = pivot[ventanas_orden]

    ventanas_labels = []
    for v in ventanas_orden:
        hora = int(v.split(":")[0])
        if HORA_LABORAL_INICIO <= hora < HORA_LABORAL_FIN:
            ventanas_labels.append(f"✅ {v}")
        else:
            ventanas_labels.append(f"⬛ {v}")

    fig_heat = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=ventanas_labels,
        y=pivot.index.tolist(),
        colorscale=[
            [0.0, "#1e1e2f"],
            [0.25, "#2d2b55"],
            [0.5, "#5c6bc0"],
            [0.75, "#82b1ff"],
            [1.0, "#b388ff"],
        ],
        hovertemplate="Vendedor: %{y}<br>Franja: %{x}<br>Llamadas: %{z}<extra></extra>",
    ))

    fig_heat.update_layout(
        title=dict(text="Densidad de Llamadas por Vendedor y Franja Horaria (30 min)", font=dict(size=14)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0", family="Inter"),
        xaxis=dict(title="", tickangle=-45, side="bottom"),
        yaxis=dict(title="", type="category", autorange="reversed"),
        height=max(350, len(pivot) * 30 + 100),
        margin=dict(t=50, b=120, l=20, r=20),
    )

    # 4. Heatmap 2: Frecuencia Semanal
    df_display["_Dia_Semana_Num"] = pd.to_datetime(df_display[COL_INICIO]).dt.weekday
    df_display["_Dia_Semana"] = df_display["_Dia_Semana_Num"].map({
        0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
        4: "Viernes", 5: "Sábado", 6: "Domingo"
    })

    semanal_data = df_display.groupby(["_Vendedor", "_Dia_Semana"]).size().reset_index(name="Llamadas")
    dias_orden = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    semanal_data["_Dia_Semana"] = pd.Categorical(semanal_data["_Dia_Semana"], categories=dias_orden, ordered=True)

    pivot_semanal = semanal_data.pivot_table(
        index="_Vendedor",
        columns="_Dia_Semana",
        values="Llamadas",
        aggfunc="sum",
        fill_value=0,
    )

    for d in dias_orden:
        if d not in pivot_semanal.columns:
            pivot_semanal[d] = 0
    pivot_semanal = pivot_semanal[dias_orden]

    fig_heat_sem = go.Figure(data=go.Heatmap(
        z=pivot_semanal.values,
        x=pivot_semanal.columns.tolist(),
        y=pivot_semanal.index.tolist(),
        colorscale=[
            [0.0, "#1e1e2f"],
            [0.25, "#2d2b55"],
            [0.5, "#5c6bc0"],
            [0.75, "#82b1ff"],
            [1.0, "#b388ff"],
        ],
        hovertemplate="Vendedor: %{y}<br>Día: %{x}<br>Llamadas: %{z}<extra></extra>",
    ))

    fig_heat_sem.update_layout(
        title=dict(text="Llamadas por Vendedor según Día de la Semana", font=dict(size=14)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0", family="Inter"),
        xaxis=dict(title=""),
        yaxis=dict(title="", type="category", autorange="reversed"),
        height=max(350, len(pivot_semanal) * 30 + 100),
        margin=dict(t=50, b=50, l=20, r=20),
    )

    # 5. Heatmap 3: Frecuencia por Fecha
    fig_heat_mensual = None
    fechas_unicas = sorted(df_display["_Fecha"].dropna().unique().tolist())
    
    if len(fechas_unicas) > 0:
        rango_fechas = pd.date_range(start=min(fechas_unicas), end=max(fechas_unicas)).date.tolist()
        
        mensual_data = df_display.groupby(["_Vendedor", "_Fecha"]).size().reset_index(name="Llamadas")
        pivot_mensual = mensual_data.pivot_table(
            index="_Vendedor",
            columns="_Fecha",
            values="Llamadas",
            aggfunc="sum",
            fill_value=0,
        )

        for d in rango_fechas:
            if d not in pivot_mensual.columns:
                pivot_mensual[d] = 0
        pivot_mensual = pivot_mensual[rango_fechas]
        fechas_labels = [f"{dias_abrev[d.weekday()]} {d.day:02d}/{d.month:02d}" for d in rango_fechas]

        fig_heat_mensual = go.Figure(data=go.Heatmap(
            z=pivot_mensual.values,
            x=fechas_labels,
            y=pivot_mensual.index.tolist(),
            colorscale=[
                [0.0, "#1e1e2f"],
                [0.25, "#2d2b55"],
                [0.5, "#5c6bc0"],
                [0.75, "#82b1ff"],
                [1.0, "#b388ff"],
            ],
            hovertemplate="Vendedor: %{y}<br>Fecha: %{x}<br>Llamadas: %{z}<extra></extra>",
        ))

        fig_heat_mensual.update_layout(
            title=dict(text="Llamadas por Vendedor según Fecha del Reporte", font=dict(size=14)),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e0e0e0", family="Inter"),
            xaxis=dict(title="", tickangle=-45),
            yaxis=dict(title="", type="category", autorange="reversed"),
            height=max(350, len(pivot_mensual) * 30 + 100),
            margin=dict(t=50, b=100, l=20, r=20),
        )

    # ════════════════════════════════════════
    # RENDERIZADO UI: KPIs
    # ════════════════════════════════════════
    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
    with k1:
        st.markdown(render_kpi_card(f"{total:,}", "Total Llamadas", "total"), unsafe_allow_html=True)
        if st.button("🔎 Ver todas", key="btn_kpi_total", use_container_width=True):
            st.session_state["llamados_filtro_kpi"] = "Todas"
            st.rerun()
    with k2:
        st.markdown(render_kpi_card(f"{nros_distintos:,}", "Nros Distintos", "total"), unsafe_allow_html=True)
        if st.button("🔎 Ver distintos", key="btn_kpi_distintos", use_container_width=True):
            st.session_state["llamados_filtro_kpi"] = "Nros Distintos"
            st.rerun()
    with k3:
        st.markdown(render_kpi_card(f"{int(contestadas):,}", "Contestadas", "verde"), unsafe_allow_html=True)
        if st.button("🔎 Ver contestadas", key="btn_kpi_contestadas", use_container_width=True):
            st.session_state["llamados_filtro_kpi"] = "Contestadas"
            st.rerun()
    with k4:
        st.markdown(render_kpi_card(f"{pct_efectividad:.1f}%", "Efectividad", "amarillo"), unsafe_allow_html=True)
        if st.button("🔎 Ver no contestadas", key="btn_kpi_no_contestadas", use_container_width=True):
            st.session_state["llamados_filtro_kpi"] = "No Contestadas"
            st.rerun()
    with k5:
        st.markdown(render_kpi_card(f"{dur_min}:{dur_seg:02d}", "Duración Prom.", "total"), unsafe_allow_html=True)
        st.markdown("<div style='height:45px;'></div>", unsafe_allow_html=True)
    with k6:
        st.markdown(render_kpi_card(f"{int(fuera_horario):,}", "Fuera de Horario", "rojo"), unsafe_allow_html=True)
        if st.button("🔎 Ver fuera horario", key="btn_kpi_fuera", use_container_width=True):
            st.session_state["llamados_filtro_kpi"] = "Fuera de Horario"
            st.rerun()
    with k7:
        st.markdown(render_kpi_card(f"{intentos_lead:.2f}", "Intentos x Nro", "total"), unsafe_allow_html=True)
        st.markdown("<div style='height:45px;'></div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Barra de descarga de PDF (Screenshot style horizontal) ──
    col_pdf1, col_pdf2 = st.columns([3, 1])
    with col_pdf1:
        st.markdown(f"**Vista activa:** `{filtro_tipo}` · Vendedor: `{filtro_vendedor}` · Filtro Tabla: `{filtro_kpi}`")
    with col_pdf2:
        metricas_export = {
            "total": total,
            "nros_distintos": nros_distintos,
            "contestadas": contestadas,
            "pct_efectividad": pct_efectividad,
            "duracion_prom": f"{dur_min}:{dur_seg:02d}",
            "fuera_horario": fuera_horario,
            "intentos_lead": intentos_lead
        }
        
        st.download_button(
            label="📥 Descargar Informe PDF",
            data=lambda: generar_pdf_llamados(
                vendedor=filtro_vendedor,
                metricas=metricas_export,
                filtro_estado=filtro_tipo,
                fig_pie=fig_pie,
                fig_diario=fig_diario,
                fig_heat=fig_heat,
                fig_heat_sem=fig_heat_sem,
                fig_heat_mensual=fig_heat_mensual
            ),
            file_name=f"informe_llamados_{filtro_vendedor.replace(' ', '_')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════
    # RENDERIZADO UI: Gráficos Fila 1
    # ════════════════════════════════════════
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.plotly_chart(fig_pie, use_container_width=True)
    with col_g2:
        if fig_diario is not None:
            st.plotly_chart(fig_diario, use_container_width=True)
        else:
            st.info("No hay llamadas suficientes para graficar la distribución diaria.")

    # ════════════════════════════════════════
    # RENDERIZADO UI: Mapas de Calor
    # ════════════════════════════════════════
    st.markdown('<div class="section-header">📊 Mapas de Calor de Productividad y Patrones</div>', unsafe_allow_html=True)
    st.markdown(
        "Navegá por las pestañas para analizar la actividad de los vendedores por franja horaria, "
        "días de la semana y fechas del mes para identificar patrones de inactividad o teletrabajo."
    )

    tab_30min, tab_semanal, tab_mensual = st.tabs([
        "🕐 Franjas Horarias (30 min)",
        "📅 Frecuencia Semanal",
        "🗓️ Frecuencia por Fecha"
    ])

    with tab_30min:
        st.plotly_chart(fig_heat, use_container_width=True)

    with tab_semanal:
        st.plotly_chart(fig_heat_sem, use_container_width=True)

    with tab_mensual:
        if fig_heat_mensual is not None:
            st.plotly_chart(fig_heat_mensual, use_container_width=True)
        else:
            st.info("No se encontraron fechas válidas para graficar.")

    # ════════════════════════════════════════
    # RENDERIZADO UI: Tabla de Detalle
    # ════════════════════════════════════════
    st.markdown("---")
    st.markdown('<div class="section-header">📋 Detalle de Llamadas</div>', unsafe_allow_html=True)

    if filtro_kpi == "Contestadas":
        st.info("ℹ️ Listado filtrado por llamadas **Contestadas**. Hacé clic en **Ver todas** en los KPIs para restablecer.")
    elif filtro_kpi == "No Contestadas":
        st.info("ℹ️ Listado filtrado por llamadas **No Contestadas**. Hacé clic en **Ver todas** en los KPIs para restablecer.")
    elif filtro_kpi == "Fuera de Horario":
        st.info("ℹ️ Listado filtrado por llamadas **Fuera de Horario**. Hacé clic en **Ver todas** en los KPIs para restablecer.")
    elif filtro_kpi == "Nros Distintos":
        st.info("ℹ️ Listado filtrado por **Números Únicos de Destino**. Muestra la primera llamada realizada a cada número. Hacé clic en **Ver todas** para restablecer.")

    detail_cols = [
        "_Vendedor", COL_DESTINO, COL_INICIO, COL_DURACION,
        COL_CONVERSACION, COL_ESTADO, COL_TIPO, COL_RESPONDIDA, COL_ACCION,
    ]
    existing_detail = [c for c in detail_cols if c in df_detalle.columns]

    st.dataframe(
        df_detalle[existing_detail],
        use_container_width=True,
        height=450,
    )


def main():
    # ══════════════════════════════════════════
    # NAVEGACIÓN LATERAL (SIDEBAR)
    # ══════════════════════════════════════════
    with st.sidebar:
        st.markdown("""
        <div style="text-align:center; padding: 10px 0 15px 0;">
            <h2 style="margin:0; font-size:1.4rem; font-weight:800;
                       background: linear-gradient(135deg, #82b1ff, #b388ff);
                       -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                🚀 Portal Comercial
            </h2>
            <p style="color:#a0a0b8; font-size:0.75rem; margin-top:4px;">
                Gestión integral de ventas y operaciones
            </p>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("---")

        modulo = st.radio(
            "🧭 Seleccioná un módulo:",
            ["🎯 Auditoría de Ventas", "📞 Control de Llamados"],
            index=0,
            key="nav_modulo",
        )

        st.markdown("---")
        st.caption("v3.0 — Portal Multipropósito")

    # ══════════════════════════════════════════
    # MÓDULO 1: AUDITORÍA DE VENTAS
    # ══════════════════════════════════════════
    if modulo == "🎯 Auditoría de Ventas":
        # ── Encabezado principal ──
        st.markdown("""
        <div style="text-align:center; padding: 10px 0 25px 0;">
            <h1 style="margin:0; font-size:2.2rem; font-weight:800;
                       background: linear-gradient(135deg, #82b1ff, #b388ff);
                       -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                📊 Sistema de Auditoría de Ventas y Retención
            </h1>
            <p style="color:#a0a0b8; font-size:0.95rem; margin-top:4px;">
                Control de calidad comercial · Análisis de Churn ≤ 60 días · Generación de informes
            </p>
        </div>
        """, unsafe_allow_html=True)

        # ── Pestañas principales ──
        tab1, tab2, tab3, tab4 = st.tabs([
            "📁  Carga y Dashboard",
            "⚙️  Gestión y Persistencia",
            "📄  Generador de Informes PDF",
            "⚡  Configuración de Reglas",
        ])

        # ─────────────────────────────────────────
        # PESTAÑA 1: CARGA Y DASHBOARD
        # ─────────────────────────────────────────
        with tab1:
            st.markdown('<div class="section-header">📂 Carga de Archivos CSV</div>', unsafe_allow_html=True)

            col_up1, col_up2, col_up3 = st.columns(3)
            with col_up1:
                file_ventas = st.file_uploader(
                    "**Ventas** — Origen de la gestión",
                    type=["csv", "xlsx", "xls"],
                    key="ventas",
                    help="Columnas esperadas: Código, Nombre, Asignación, Fecha alta",
                )
            with col_up2:
                file_db = st.file_uploader(
                    "**DB** — Estado operativo actual",
                    type=["csv", "xlsx", "xls"],
                    key="db",
                    help="Columnas esperadas: Código, Estado, Deuda, Deuda vencida, Fecha de bloqueo",
                )
            with col_up3:
                file_bajas = st.file_uploader(
                    "**Bajas** — Gestión de retiros",
                    type=["csv", "xlsx", "xls"],
                    key="bajas",
                    help="Columnas esperadas: Código, Categoría, Fecha",
                )

            if file_ventas and file_db and file_bajas:
                # Leer CSVs
                try:
                    dtype_codigos = {"Código": str, "Codigo": str}
                    df_ventas = leer_archivo(file_ventas, dtype=dtype_codigos)
                    df_db = leer_archivo(file_db, dtype=dtype_codigos)
                    df_bajas = leer_archivo(file_bajas, dtype=dtype_codigos)
                except Exception as e:
                    st.error(f"❌ Error al leer los archivos CSV: {e}")
                    return

                # Procesar
                with st.spinner("🔄 Procesando datos y aplicando reglas de auditoría..."):
                    df_merged = procesar_datos(df_ventas, df_db, df_bajas)
                    st.session_state["df_merged"] = df_merged

                st.success(f"✅ Se procesaron **{len(df_merged)}** registros correctamente.")

                st.markdown("---")
                st.markdown('<div class="section-header">📊 Dashboard Ejecutivo</div>', unsafe_allow_html=True)

                # ── Filtro por vendedor ──
                vendedores_list = sorted(df_merged["Asignación"].dropna().unique().tolist())
                filtro_vendedor = st.selectbox(
                    "🔍 Filtrar por vendedor:",
                    options=["Todos"] + vendedores_list,
                    index=0,
                    key="filtro_vendedor_dash",
                )

                # Aplicar filtro
                if filtro_vendedor != "Todos":
                    df_display = df_merged[df_merged["Asignación"] == filtro_vendedor].copy()
                else:
                    df_display = df_merged.copy()

                # ── KPIs ──
                total = len(df_display)
                verdes = len(df_display[df_display["Semáforo"] == "🟢 VERDE"])
                amarillos = len(df_display[df_display["Semáforo"] == "🟡 AMARILLO"])
                rojos = len(df_display[df_display["Semáforo"] == "🔴 ROJO"])
                negros = len(df_display[df_display["Semáforo"] == "⚫ NEGRO"])
                penalizables = len(df_display[
                    (df_display["Semáforo"] == "⚫ NEGRO") &
                    (df_display["Días Activos"].notna()) &
                    (df_display["Días Activos"] <= UMBRAL_DIAS) &
                    (~df_display["Ya_Descontado"])
                ])
                ya_desc = len(df_display[df_display["Ya_Descontado"]])

                k1, k2, k3, k4, k5, k6 = st.columns(6)
                with k1:
                    st.markdown(render_kpi_card(total, "Ventas Totales", "total"), unsafe_allow_html=True)
                    if st.button("Ver todos", key="btn_total", use_container_width=True):
                        st.session_state["dash_filtro"] = "Todos"
                with k2:
                    st.markdown(render_kpi_card(verdes, "Validadas (Verde)", "verde"), unsafe_allow_html=True)
                    if st.button("Ver verdes", key="btn_verde", use_container_width=True):
                        st.session_state["dash_filtro"] = "🟢 VERDE"
                with k3:
                    st.markdown(render_kpi_card(amarillos, "Morosidad (Amarillo)", "amarillo"), unsafe_allow_html=True)
                    if st.button("Ver amarillos", key="btn_amarillo", use_container_width=True):
                        st.session_state["dash_filtro"] = "🟡 AMARILLO"
                with k4:
                    st.markdown(render_kpi_card(rojos, "Críticas (Rojo)", "rojo"), unsafe_allow_html=True)
                    if st.button("Ver rojos", key="btn_rojo", use_container_width=True):
                        st.session_state["dash_filtro"] = "🔴 ROJO"
                with k5:
                    st.markdown(render_kpi_card(penalizables, "Penalizables (Negro)", "negro"), unsafe_allow_html=True)
                    if st.button("Ver penalizables", key="btn_penal", use_container_width=True):
                        st.session_state["dash_filtro"] = "PENALIZABLE"
                with k6:
                    st.markdown(render_kpi_card(ya_desc, "Ya Descontados", "total"), unsafe_allow_html=True)
                    if st.button("Ver descontados", key="btn_desc", use_container_width=True):
                        st.session_state["dash_filtro"] = "YA_DESCONTADO"

                st.markdown("<br>", unsafe_allow_html=True)

                # ── Gráficos ──
                col_g1, col_g2 = st.columns(2)

                color_map = {
                    "🟢 VERDE": "#00e676",
                    "🟡 AMARILLO": "#ffea00",
                    "🔴 ROJO": "#ff5252",
                    "⚫ NEGRO": "#78909c",
                    "⬜ EXCLUIDO": "#546e7a",
                }

                with col_g1:
                    # Distribución general del semáforo
                    sem_counts = df_display["Semáforo"].value_counts().reset_index()
                    sem_counts.columns = ["Semáforo", "Cantidad"]
                    fig_pie = px.pie(
                        sem_counts,
                        values="Cantidad",
                        names="Semáforo",
                        color="Semáforo",
                        color_discrete_map=color_map,
                        hole=0.45,
                    )
                    fig_pie.update_layout(
                        title=dict(text="Distribución del Semáforo", font=dict(size=14)),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#e0e0e0", family="Inter"),
                        legend=dict(font=dict(size=10)),
                        height=380,
                        margin=dict(t=50, b=20, l=20, r=20),
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)

                with col_g2:
                    # Rendimiento por vendedor
                    vendedor_data = df_display.groupby(["Asignación", "Semáforo"]).size().reset_index(name="Cantidad")
                    fig_bar = px.bar(
                        vendedor_data,
                        x="Asignación",
                        y="Cantidad",
                        color="Semáforo",
                        color_discrete_map=color_map,
                        barmode="stack",
                    )
                    fig_bar.update_layout(
                        title=dict(text="Rendimiento por Vendedor", font=dict(size=14)),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#e0e0e0", family="Inter"),
                        xaxis=dict(title="", tickangle=-45),
                        yaxis=dict(title="Clientes", gridcolor="rgba(255,255,255,0.05)"),
                        legend=dict(font=dict(size=10)),
                        height=380,
                        margin=dict(t=50, b=80, l=40, r=20),
                    )
                    st.plotly_chart(fig_bar, use_container_width=True)

                # ── Tabla dinámica por filtro de KPI ──
                display_cols = [
                    "Código", "Nombre", "Asignación", "Fecha alta",
                    "Estado", "Deuda vencida", "Días Activos",
                    "Semáforo", "Penalización", "Motivo Baja", "Fecha de baja"
                ]
                existing_cols = [c for c in display_cols if c in df_display.columns]

                filtro_activo = st.session_state.get("dash_filtro", None)

                if filtro_activo:
                    # Aplicar filtro según KPI seleccionado
                    if filtro_activo == "Todos":
                        df_filtrado = df_display
                        titulo_filtro = "📋 Todos los clientes"
                    elif filtro_activo == "PENALIZABLE":
                        df_filtrado = df_display[
                            (df_display["Semáforo"] == "⚫ NEGRO") &
                            (df_display["Días Activos"].notna()) &
                            (df_display["Días Activos"] <= UMBRAL_DIAS) &
                            (~df_display["Ya_Descontado"])
                        ]
                        titulo_filtro = "🚨 Clientes Penalizables (Negro < 60 días)"
                    elif filtro_activo == "YA_DESCONTADO":
                        df_filtrado = df_display[df_display["Ya_Descontado"]]
                        titulo_filtro = "✅ Clientes Ya Descontados"
                    else:
                        df_filtrado = df_display[df_display["Semáforo"] == filtro_activo]
                        titulo_filtro = f"Clientes en estado: {filtro_activo}"

                    st.markdown("---")
                    col_title, col_clear = st.columns([4, 1])
                    with col_title:
                        st.markdown(f'<div class="section-header">{titulo_filtro} ({len(df_filtrado)})</div>', unsafe_allow_html=True)
                    with col_clear:
                        if st.button("✖ Cerrar vista", key="btn_clear_filter", use_container_width=True):
                            st.session_state["dash_filtro"] = None
                            st.rerun()

                    if len(df_filtrado) > 0:
                        st.dataframe(
                            df_filtrado[existing_cols],
                            use_container_width=True,
                            height=min(450, 60 + len(df_filtrado) * 35),
                        )
                    else:
                        st.info("No se encontraron clientes con este filtro.")

                # ── Tabla resumen completa (expandible) ──
                with st.expander("📋 Ver tabla completa de auditoría", expanded=False):
                    st.dataframe(
                        df_display[existing_cols],
                        use_container_width=True,
                        height=400,
                    )

            else:
                st.info("👆 Cargá los 3 archivos (CSV o XLSX) para comenzar la auditoría.")

        # ─────────────────────────────────────────
        # PESTAÑA 2: GESTIÓN Y PERSISTENCIA
        # ─────────────────────────────────────────
        with tab2:
            st.markdown('<div class="section-header">⚙️ Gestión de Penalizaciones</div>', unsafe_allow_html=True)

            if "df_merged" not in st.session_state:
                st.warning("⚠️ Primero cargá los archivos CSV en la pestaña **Carga y Dashboard**.")
            else:
                df_merged = st.session_state["df_merged"]

                # Filtrar: Solo NEGRO, penalizables (<=60 días), NO ya descontados
                df_penalizables = df_merged[
                    (df_merged["Semáforo"] == "⚫ NEGRO") &
                    (df_merged["Días Activos"].notna()) &
                    (df_merged["Días Activos"] <= UMBRAL_DIAS) &
                    (~df_merged["Ya_Descontado"])
                ].copy()

                if len(df_penalizables) == 0:
                    st.success("🎉 No hay clientes pendientes de penalización en este período.")
                else:
                    st.markdown(
                        f"Se encontraron **{len(df_penalizables)}** clientes en situación "
                        f"⚫ NEGRO con menos de {UMBRAL_DIAS} días activos, pendientes de descuento."
                    )

                    # Preparar DataFrame para el editor
                    df_editor = df_penalizables[[
                        "Código", "Nombre", "Asignación", "Fecha alta",
                        "Días Activos", "Motivo Baja", "Deuda vencida",
                    ]].copy()
                    df_editor.insert(0, "Aplicar Descuento", False)
                    df_editor = df_editor.reset_index(drop=True)

                    edited_df = st.data_editor(
                        df_editor,
                        use_container_width=True,
                        height=min(400, 60 + len(df_editor) * 35),
                        column_config={
                            "Aplicar Descuento": st.column_config.CheckboxColumn(
                                "✅ Aplicar",
                                help="Seleccionar para registrar el descuento",
                                default=False,
                            ),
                            "Código": st.column_config.TextColumn("Código", disabled=True),
                            "Nombre": st.column_config.TextColumn("Cliente", disabled=True),
                            "Asignación": st.column_config.TextColumn("Vendedor", disabled=True),
                            "Días Activos": st.column_config.NumberColumn("Días", disabled=True, format="%d"),
                            "Deuda vencida": st.column_config.NumberColumn("Deuda Venc.", disabled=True, format="$%.2f"),
                        },
                        key="penalty_editor",
                        num_rows="fixed",
                    )

                    st.markdown("---")

                    # Botón de confirmación
                    seleccionados = edited_df[edited_df["Aplicar Descuento"] == True]  # noqa: E712
                    n_sel = len(seleccionados)

                    col_btn, col_info = st.columns([1, 2])
                    with col_btn:
                        confirmar = st.button(
                            f"💾 Confirmar y Guardar Descuentos ({n_sel})",
                            type="primary",
                            disabled=(n_sel == 0),
                            use_container_width=True,
                        )
                    with col_info:
                        if n_sel > 0:
                            st.info(f"Se registrarán **{n_sel}** descuentos en la base de datos.")
                        else:
                            st.caption("Seleccioná al menos un cliente para habilitar el guardado.")

                    if confirmar and n_sel > 0:
                        registros = [
                            {"codigo": row["Código"], "vendedor": row["Asignación"]}
                            for _, row in seleccionados.iterrows()
                        ]
                        insertar_descuentos(registros)

                        # Actualizar el DataFrame en session_state
                        codigos_insertados = {r["codigo"] for r in registros}
                        df_merged.loc[df_merged["Código"].isin(codigos_insertados), "Ya_Descontado"] = True
                        df_merged.loc[
                            df_merged["Código"].isin(codigos_insertados), "Penalización"
                        ] = "✅ Ya descontado históricamente"
                        st.session_state["df_merged"] = df_merged

                        st.success(f"✅ Se registraron {n_sel} descuentos exitosamente en la base de datos.")
                        st.rerun()

                # ── Historial de descuentos ──
                st.markdown("---")
                st.markdown('<div class="section-header">📚 Historial de Descuentos Aplicados</div>', unsafe_allow_html=True)
                df_hist = get_historial_descuentos()
                if len(df_hist) == 0:
                    st.caption("Aún no se han registrado descuentos en la base de datos.")
                else:
                    st.dataframe(
                        df_hist,
                        use_container_width=True,
                        column_config={
                            "codigo_cliente": "Código Cliente",
                            "fecha_registro": st.column_config.DatetimeColumn("Fecha Registro", format="DD/MM/YYYY HH:mm"),
                            "vendedor": "Vendedor",
                        },
                        height=min(300, 60 + len(df_hist) * 35),
                    )
                    st.caption(f"Total de descuentos registrados: **{len(df_hist)}**")

        # ─────────────────────────────────────────
        # PESTAÑA 3: GENERADOR DE INFORMES PDF
        # ─────────────────────────────────────────
        with tab3:
            st.markdown('<div class="section-header">📄 Generador de Informes PDF por Vendedor</div>', unsafe_allow_html=True)

            if "df_merged" not in st.session_state:
                st.warning("⚠️ Primero cargá los archivos CSV en la pestaña **Carga y Dashboard**.")
            else:
                df_merged = st.session_state["df_merged"]
                vendedores = sorted(df_merged["Asignación"].dropna().unique().tolist())

                if len(vendedores) == 0:
                    st.warning("No se encontraron vendedores en los datos.")
                else:
                    col_sel, col_btn = st.columns([2, 1])

                    with col_sel:
                        vendedor_sel = st.selectbox(
                            "Seleccioná un vendedor:",
                            options=vendedores,
                            index=0,
                            key="pdf_vendedor",
                        )

                    df_vend = df_merged[df_merged["Asignación"] == vendedor_sel].copy()

                    # Métricas del vendedor
                    m_total = len(df_vend)
                    m_verdes = len(df_vend[df_vend["Semáforo"] == "🟢 VERDE"])
                    m_amarillos = len(df_vend[df_vend["Semáforo"] == "🟡 AMARILLO"])
                    m_rojos = len(df_vend[df_vend["Semáforo"] == "🔴 ROJO"])
                    m_negros = len(df_vend[df_vend["Semáforo"] == "⚫ NEGRO"])
                    m_penalizables = len(df_vend[
                        (df_vend["Semáforo"] == "⚫ NEGRO") &
                        (df_vend["Días Activos"].notna()) &
                        (df_vend["Días Activos"] <= UMBRAL_DIAS) &
                        (~df_vend["Ya_Descontado"])
                    ])
                    m_alertas = m_amarillos + m_rojos

                    # Preview de métricas
                    st.markdown(f"### Resumen: {vendedor_sel}")
                    mk1, mk2, mk3, mk4, mk5 = st.columns(5)
                    mk1.metric("Total Ventas", m_total)
                    mk2.metric("🟢 Verdes", m_verdes)
                    mk3.metric("🟡🔴 En Alerta", m_alertas)
                    mk4.metric("⚫ Negros", m_negros)
                    mk5.metric("🚨 Penalizables", m_penalizables)

                    # Preview de clientes en alerta/baja ordenados
                    semaforo_orden = {"⚫ NEGRO": 1, "🔴 ROJO": 2, "🟡 AMARILLO": 3}
                    df_alertas = df_vend[df_vend["Semáforo"].isin(semaforo_orden.keys())].copy()
                    df_alertas["_Orden"] = df_alertas["Semáforo"].map(semaforo_orden)
                    df_alertas = df_alertas.sort_values(by=["_Orden", "Deuda vencida"], ascending=[True, False])

                    df_preview = df_alertas[[
                        "Código", "Nombre", "Fecha alta", "Días Activos",
                        "Estado", "Semáforo", "Motivo Baja", "Fecha de baja"
                    ]].copy()

                    if len(df_preview) > 0:
                        st.markdown("**Vista previa de clientes incluidos en el informe:**")
                        st.dataframe(df_preview, use_container_width=True, height=min(250, 60 + len(df_preview) * 35))
                    else:
                        st.info("Este vendedor no tiene clientes en alerta o baja. El PDF se generará vacío en su tabla de detalle.")

                    st.markdown("---")

                    with col_btn:
                        st.markdown("<br>", unsafe_allow_html=True)
                        generar = st.button(
                            "📥 Exportar Informe PDF",
                            type="primary",
                            use_container_width=True,
                            key="btn_pdf",
                        )

                    if generar:
                        metricas = {
                            "total": m_total,
                            "verdes": m_verdes,
                            "alertas": m_alertas,
                            "penalizables": m_penalizables,
                        }
                        with st.spinner("📝 Generando informe PDF..."):
                            pdf_bytes = generar_pdf(vendedor_sel, df_vend, metricas)

                        nombre_archivo = f"Informe_Auditoria_{vendedor_sel.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"

                        st.download_button(
                            label="⬇️ Descargar PDF",
                            data=bytes(pdf_bytes),
                            file_name=nombre_archivo,
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True,
                        )
                        st.success(f"✅ Informe generado: **{nombre_archivo}**")

        # ─────────────────────────────────────────
        # PESTAÑA 4: CONFIGURACIÓN DE REGLAS
        # ─────────────────────────────────────────
        with tab4:
            st.markdown('<div class="section-header">⚡ Configuración del Motor de Clasificación</div>', unsafe_allow_html=True)
            st.markdown(
                "Acá podés editar las reglas que determinan si un cliente cae en Verde, Amarillo, Rojo o Negro. "
                "Las reglas se evalúan **de arriba hacia abajo (orden de Prioridad)**. La primera regla que coincida con los datos "
                "del cliente, definirá su situación."
            )

            # Cargar reglas actuales
            df_reglas = get_reglas()

            # Opciones para los dropdowns de la tabla
            opciones_situacion = ["🟢 VERDE", "🟡 AMARILLO", "🔴 ROJO", "⚫ NEGRO", "⬜ EXCLUIDO"]
            opciones_estado = ["cualquiera", "habilitado", "bloqueado", "sin servicio"]
            opciones_deuda = ["cualquiera", "= 0", "> 0"]
            opciones_binarias = ["cualquiera", "sí", "no"]

            # Configurar las columnas del editor
            config_columnas = {
                "prioridad": st.column_config.NumberColumn(
                    "Prioridad",
                    help="Orden de evaluación (menor número = mayor prioridad)",
                    min_value=1,
                    max_value=999,
                    step=10,
                    required=True,
                ),
                "situacion": st.column_config.SelectboxColumn(
                    "Situación (Resultado)",
                    options=opciones_situacion,
                    required=True,
                ),
                "estado": st.column_config.SelectboxColumn(
                    "Estado DB",
                    options=opciones_estado,
                    required=True,
                ),
                "deuda_vencida": st.column_config.SelectboxColumn(
                    "Deuda Vencida",
                    options=opciones_deuda,
                    required=True,
                ),
                "en_bajas": st.column_config.SelectboxColumn(
                    "¿Está en Bajas.csv?",
                    options=opciones_binarias,
                    required=True,
                ),
                "tiene_fecha_bloqueo": st.column_config.SelectboxColumn(
                    "¿Tiene Fecha Bloqueo?",
                    options=opciones_binarias,
                    required=True,
                ),
                "descripcion": st.column_config.TextColumn(
                    "Descripción / Motivo",
                    help="Nombre de la regla para identificar por qué se penaliza",
                    max_chars=100,
                    required=True,
                ),
            }

            with st.form("form_reglas"):
                df_reglas_editadas = st.data_editor(
                    df_reglas,
                    use_container_width=True,
                    num_rows="dynamic",
                    column_config=config_columnas,
                    hide_index=True,
                    height=400,
                )

                btn_guardar_reglas = st.form_submit_button("💾 Guardar y Aplicar Reglas", type="primary")

            if btn_guardar_reglas:
                # Validar que no haya prioridades duplicadas
                if df_reglas_editadas["prioridad"].duplicated().any():
                    st.error("❌ Hay reglas con el mismo número de prioridad. Por favor, usá números distintos.")
                else:
                    try:
                        save_reglas(df_reglas_editadas)
                        st.success("✅ Reglas guardadas correctamente. El dashboard utilizará esta nueva configuración.")
                        # Limpiar estado del dashboard para forzar reproceso
                        if "df_merged" in st.session_state:
                            del st.session_state["df_merged"]
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error al guardar las reglas: {str(e)}")

    # ═══════════════════════════════════════════════
    # ENTRY POINT
    # ═══════════════════════════════════════════════

    # ══════════════════════════════════════════
    # MÓDULO 2: CONTROL DE LLAMADOS
    # ══════════════════════════════════════════
    elif modulo == "📞 Control de Llamados":
        modulo_llamados()

if __name__ == "__main__":
    main()
