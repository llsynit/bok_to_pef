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


def prepare_for_pef(xhtml_path, logger):

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
            "-s:" + xhtml_path,
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
        shutil.copy(temp_output, xhtml_path)
        logger.info("XSLT processing complete")
        logger.info("Changing pef-about to frontmatter")
        """with open(html_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "html.parser")
        for section in soup.find_all("section", class_="pef-about"):
            section["epub:type"] = "frontmatter"
            del section["class"]
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(str(soup))"""
        # fix_pef_about_sections(html_path)


        # ---------- plasser braille-specific-info riktig og flytt innhold fra pef-about ----------
        logger.info("Plasserer braille-specific-info riktig og flytter innhold fra pef-about")

        soup = BeautifulSoup(open(xhtml_path, "r", encoding="utf-8"), "html.parser")

        pef_title = soup.find("section", class_="pef-titlepage")
        frontmatter = soup.find("section", attrs={"epub:type": "frontmatter"})
        pef_about = soup.find("section", class_="pef-about")
        braille_section = soup.find("section", class_="braille-specific-info")

        # Incase insert-boilerplate inserts the production number in a <p class="Høyre-justert">
        # Replace <p class="Høyre-justert"> which contains production number with empty paragraph <p> </p> 
        if pef_title:
            p_right = pef_title.find("p", class_="Høyre-justert")
            if p_right:
                new_p = soup.new_tag("p")
                new_p.string = "\u00A0"  # non-breaking space
                p_right.replace_with(new_p)
                logger.info("Erstattet p produksjonsnummer med tom <p> </p>.")
            else:
                logger.info("Fant ikke p produksjonsnummer.")

        # Create or detach existing braille section
        if braille_section:
            braille_section.extract()
        else:
            braille_section = soup.new_tag("section", **{"class": "braille-specific-info"})

        # Placement logic (updated per your instructions)
        if pef_title:
            logger.info("Plasserer braille-specific-info rett under <section class='pef-titlepage'>")
            pef_title.insert_after(braille_section)

        elif frontmatter:
            logger.info("Ingen titlepage – plasserer rett over første <section epub:type='frontmatter'>")
            frontmatter.insert_before(braille_section)

        else:
            bodymatter = soup.find("section", attrs={"epub:type": "bodymatter"})
            if bodymatter:
                logger.info("Ingen titlepage/frontmatter – plasserer rett over <section epub:type='bodymatter'>")
                bodymatter.insert_before(braille_section)
            else:
                logger.info("Ingen titlepage, frontmatter eller bodymatter – plasserer øverst i <body>")
                if soup.body:
                    soup.body.insert(0, braille_section)
                else:
                    soup.insert(0, braille_section)

        # Move all content from pef-about into braille-specific-info
        if pef_about:
            logger.info("Flytter innhold fra pef-about inn i braille-specific-info og fjerner pef-about.")
            pef_children = list(pef_about.contents)
              # Insert each child at the beginning of braille_section, preserving order
            for child in reversed(pef_children):
                braille_section.insert(0, child.extract())
            pef_about.decompose()

        # Save to file
        with open(xhtml_path, "w", encoding="utf-8") as f:
            f.write(str(soup))

        logger.info("Ferdig med flytting av punktskrift merknader.")

        logger.info("Transformation complete")
     # Copy final XHTML to .html
    html_path = Path(xhtml_path).with_suffix(".html")
    shutil.copy2(xhtml_path, Path(xhtml_path).with_suffix(".html"))
    return {
        "success": True,
        "message": "HTML prepared for PEF conversion success.",
        "html_path": html_path
        #"html_path": str(Path(html_path).with_suffix(".html"))
    }
