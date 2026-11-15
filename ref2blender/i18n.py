"""Interface language: English by default, Catalan available.

`tr("English text")` returns the text in the chosen language. To add a language,
add a dictionary here and an entry to LANGUAGES.
"""

import bpy

LANGUAGES = [
    ("AUTO", "Automatic", "Follow Blender's interface language"),
    ("EN", "English", ""),
    ("CA", "Català", ""),
]

CA = {
    # panel
    "With your AI assistant (MCP)": "Amb el teu assistent d'IA (MCP)",
    "Connect": "Connecta",
    "Disconnect": "Desconnecta",
    "Connected on port {port} · {count} requests": "Connectat al port {port} · {count} peticions",
    "Give the reference images to your assistant": "Passa les imatges al teu assistent",
    "Safe mode is OFF": "El mode segur està DESACTIVAT",
    "Export to game": "Exporta al joc",
    "Join into a single mesh": "Uneix en una sola malla",
    "Palette (single material)": "Paleta (un sol material)",
    "Convex collision": "Col·lisió convexa",
    "LOD levels": "Nivells de LOD",
    "One file per asset": "Un fitxer per asset",
    "Model script": "Script del model",
    "Rebuild": "Reconstrueix",
    "Tables and the rest: Text Editor,": "Taules i la resta: Editor de text,",
    "With an API key": "Amb clau d'API",
    "The Claude library must be installed": "Cal instal·lar la llibreria de Claude",
    "Install the Claude library": "Instal·la la llibreria de Claude",
    "API key missing": "Falta la clau d'API",
    "Open preferences": "Obre les preferències",
    "Reference images": "Imatges de referència",
    "Description (optional)": "Descripció (opcional)",
    "Style": "Estil",
    "Max triangles": "Màxim de triangles",
    "Delete the previous model": "Esborra el model anterior",
    "Max steps": "Passos màxims",
    "Stop": "Atura",
    "Generate model": "Genera el model",
    "What do you want to change?": "Què vols canviar?",
    "Refine": "Refina",
    "Tokens: {tin} in / {tout} out": "Tokens: {tin} entrada / {tout} sortida",
    # preferences
    "Anthropic API key": "Clau d'API d'Anthropic",
    "Model": "Model",
    "Effort": "Esforç",
    "Language": "Idioma",
    "Safe mode (recommended)": "Mode segur (recomanat)",
    "Get a key at console.anthropic.com": "Aconsegueix la clau a console.anthropic.com",
    "Safe mode blocks file, process and network access in generated code.":
        "El mode segur bloqueja l'accés a fitxers, processos i xarxa del codi generat.",
    # reports
    "Installation failed. See the system console.": "La instal·lació ha fallat. Mira la consola del sistema.",
    "Library installed.": "Llibreria instal·lada.",
    "The Claude library is missing: install it from the panel.":
        "Falta la llibreria de Claude: instal·la-la des del panell.",
    "API key missing: set it in the add-on preferences.":
        "Falta la clau d'API: posa-la a les preferències de l'add-on.",
    "Could not read an image: {error}": "No s'ha pogut llegir una imatge: {error}",
    "Export failed: {error}": "No s'ha pogut exportar: {error}",
    "Exported {files} file(s), {tris} triangles in total": "Exportats {files} fitxers, {tris} triangles en total",
    "Could not open port {port}: {error}": "No s'ha pogut obrir el port {port}: {error}",
    "The script failed: {error}": "L'script ha fallat: {error}",
    "Model rebuilt: {tris} triangles": "Model reconstruït: {tris} triangles",
    # agent status
    "Idle": "Inactiu",
    "A generation is already running.": "Ja hi ha una generació en marxa.",
    "Cancelled": "Cancel·lat",
    "Step {step}/{total}: Claude is thinking…": "Pas {step}/{total}: Claude està pensant…",
    "Step {step}/{total}: running code in Blender…": "Pas {step}/{total}: executant codi a Blender…",
    "Step {step}/{total}: rendering views…": "Pas {step}/{total}: renderitzant vistes…",
    "Done ✔": "Fet ✔",
    "Error: {error}": "Error: {error}",
    "Internal error: {error}": "Error intern: {error}",
    "The model declined the request.": "El model ha rebutjat la petició.",
    "Response cut off by the token limit. Press Refine to continue.":
        "Resposta tallada pel límit de tokens. Prem Refina per continuar.",
    "Step limit reached. Press Refine to continue.": "Límit de passos assolit. Prem Refina per continuar.",
    "▶ code executed": "▶ codi executat",
    "✖ error in the code (Claude will fix it)": "✖ error al codi (Claude el corregirà)",
    "📷 views: {views}": "📷 vistes: {views}",
    "Step {step}/{total}: comparing with the reference…": "Pas {step}/{total}: comparant amb la referència…",
    "🔍 comparison with reference {index}": "🔍 comparació amb la referència {index}",
    "🔍 comparison with reference {index}: {score}% overlap":
        "🔍 comparació amb la referència {index}: {score}% de coincidència",
    "Invalid API key. Check it in the add-on preferences.":
        "Clau d'API no vàlida. Revisa-la a les preferències de l'add-on.",
    "The API key has no permission for this model.": "La clau d'API no té permís per a aquest model.",
    "Model not found. Choose another one in the preferences.":
        "Model no trobat. Tria'n un altre a les preferències.",
    "Rate limit reached. Wait a moment and press Refine to continue.":
        "Límit de peticions assolit. Espera una mica i prem Refina per continuar.",
    "Request rejected by the API: {error}": "Petició rebutjada per l'API: {error}",
    "API error ({code}): {error}": "Error de l'API ({code}): {error}",
    "Network error. Check your Internet connection.": "Error de xarxa. Comprova la connexió a Internet.",
}

_TABLES = {"CA": CA}
SUMMARY_LANGUAGE = {"EN": "English", "CA": "Catalan"}


def language():
    choice = "AUTO"
    addon = bpy.context.preferences.addons.get(__package__)
    if addon:
        choice = getattr(addon.preferences, "language", "AUTO")
    if choice == "AUTO":
        locale = (bpy.app.translations.locale or "").lower()
        return "CA" if locale.startswith("ca") else "EN"
    return choice


def tr(text, **values):
    text = _TABLES.get(language(), {}).get(text, text)
    return text.format(**values) if values else text
