import os
import subprocess
import shutil
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from fastapi.responses import JSONResponse

# SAXON_JAR = os.environ.get("SAXON_JAR")
# XSLT_DIR = os.environ.get("XSLT_DIR")
PROJECT_ROOT = Path(__file__).resolve().parent
SAXON_JAR = PROJECT_ROOT / "jar" / "saxon-he-9.5.1.5-1.jar"
JING_JAR = PROJECT_ROOT / "jar" / "jing-20161127.jar"
XSLT_DIR = PROJECT_ROOT / "xslt" / "prepare-for-braille"


def prepare_for_pef(html_path, logger):

    stylesheets = {
        "prepare-for-braille.xsl": "Tilpasser innhold for punktskrift…",
        "pre-processing.xsl": "Bedre hefteinndeling, fjern tittelside og innholdsfortegnelse, flytte kolofon og opphavsrettside til slutten av boka…",
        "add-table-classes.xsl": "Bedre håndtering av tabeller…",
        "insert-boilerplate.xsl": "Lag ny tittelside og bokinformasjon…"
    }
    temp_output = None
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_output = os.path.join(temp_dir, "output.html")
    except Exception as e:
        logger.error(f"Failed to create temporary directory: {e}")
        return {
            "success": False,
            "errors": "Failed to create temporary directory.",
        }

    for sheet, description in stylesheets.items():
        print(f"Processing stylesheet: {XSLT_DIR}/{sheet}")
        xslt_path = os.path.join(XSLT_DIR, sheet)
        print(f"Using XSLT ----------- : {xslt_path}")
        command = [
            "java", "-jar", SAXON_JAR,
            "-s:" + html_path,
            "-xsl:" + xslt_path,
            "-o:" + temp_output
        ]
        logger.info(f"XSLT: {sheet} - {description}")
        logger.info("Running XSLT")
        logger.info("Processing  %s", command)

        try:
            result = subprocess.run(
                command, check=True, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            logger.error(f"XSLT {sheet} timed out.")
            return {
                "success": False,
                "errors": f"XSLT timeout on {sheet}",
            }
        except subprocess.CalledProcessError as e:
            logger.error(f"XSLT failed on {sheet}: {e.stderr}")
            return {
                "success": False,
                "errors": f"XSLT failed on {sheet}: {e.stderr}"
            }
        except Exception:
            logger.debug(traceback.format_exc())
            logger.error(
                f"Unexpected error while running XSLT {sheet}")

            return {
                "success": False,
                "errors": f"Unexpected error during {sheet}",
            }
        shutil.copy(temp_output, html_path)
        logger.info("XSLT processing complete")
        logger.info("Changing pef-about to frontmatter")
        with open(html_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "html.parser")
        for section in soup.find_all("section", class_="pef-about"):
            section["epub:type"] = "frontmatter"
            del section["class"]
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(str(soup))
        # fix_pef_about_sections(html_path)
        logger.info("Transformation complete")
    return {
        "success": True,
        "message": "HTML prepared for PEF conversion success.",
        "xhtml_path": html_path
    }
