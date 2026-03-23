from flask import Flask, render_template, request, jsonify
import yt_dlp
import os
import subprocess

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Rutas explícitas — no dependen del PATH de Windows
NODE_PATH   = r"C:\Program Files\nodejs\node.exe"
FFMPEG_DIR  = r"C:\Users\hecto\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
FFMPEG_EXE  = os.path.join(FFMPEG_DIR, "ffmpeg.exe")

# Opciones base para todas las llamadas a yt-dlp
YDL_OPTS_BASE = {
    "quiet": True,
    "ffmpeg_location": FFMPEG_DIR,
    "extractor_args": {
        "youtube": {
            "js_runtimes": [f"nodejs:{NODE_PATH}"]
        }
    }
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/info", methods=["POST"])
def get_info():
    """Obtiene los formatos disponibles para una URL."""
    url = request.json.get("url")
    if not url:
        return jsonify({"error": "URL no proporcionada"}), 400

    try:
        with yt_dlp.YoutubeDL({**YDL_OPTS_BASE}) as ydl:
            info = ydl.extract_info(url, download=False)

        formatos = []
        seen = set()

        for f in info.get("formats", []):
            ext = f.get("ext")
            height = f.get("height")
            vcodec = f.get("vcodec", "none")

            if vcodec != "none" and height:
                # Formato original
                key = (height, ext)
                if key not in seen:
                    seen.add(key)
                    formatos.append({
                        "label": f"{height}p — {ext.upper()}",
                        "height": height,
                        "ext": ext,
                        "wmv": False
                    })

                # Opción WMV para cada calidad
                key_wmv = (height, "wmv")
                if key_wmv not in seen:
                    seen.add(key_wmv)
                    formatos.append({
                        "label": f"{height}p — WMV (convertido)",
                        "height": height,
                        "ext": ext,
                        "wmv": True
                    })

        # Ordenar por calidad descendente, WMV al final de cada grupo
        formatos.sort(key=lambda x: (x["height"], 0 if not x["wmv"] else -1), reverse=True)

        return jsonify({
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "formats": formatos
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download", methods=["POST"])
def download():
    """Descarga el video con el formato seleccionado."""
    data = request.json
    url = data.get("url")
    height = data.get("height")
    ext = data.get("ext")
    is_wmv = data.get("wmv", False)

    if not url or not height or not ext:
        return jsonify({"error": "Faltan parámetros"}), 400

    try:
        if is_wmv:
            # Paso 1: Descargar en MP4
            temp_opts = {
                **YDL_OPTS_BASE,
                "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
                "outtmpl": f"{DOWNLOAD_FOLDER}/%(title)s_temp.%(ext)s",
                "merge_output_format": "mp4",
            }

            with yt_dlp.YoutubeDL(temp_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                temp_filename = ydl.prepare_filename(info)
                temp_filename = os.path.splitext(temp_filename)[0] + ".mp4"

            # Paso 2: Convertir a WMV usando FFmpeg directamente
            wmv_filename = temp_filename.replace("_temp.mp4", ".wmv")

            subprocess.run([
                FFMPEG_EXE,
                "-i", temp_filename,
                "-vcodec", "wmv2",
                "-acodec", "wmav2",
                "-b:v", "1000k",
                "-b:a", "128k",
                wmv_filename,
                "-y"
            ], check=True)

            # Paso 3: Eliminar archivo temporal
            if os.path.exists(temp_filename):
                os.remove(temp_filename)

            filename = wmv_filename

        else:
            ydl_opts = {
                **YDL_OPTS_BASE,
                "format": f"bestvideo[height<={height}][ext={ext}]+bestaudio/best[height<={height}]",
                "outtmpl": f"{DOWNLOAD_FOLDER}/%(title)s.%(ext)s",
                "merge_output_format": ext,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)

        return jsonify({
            "success": True,
            "message": f"✅ Descargado: {os.path.basename(filename)}"
        })

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Error al convertir a WMV: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)