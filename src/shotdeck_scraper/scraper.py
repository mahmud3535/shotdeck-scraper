"""Shotdeck scraper.

Scrape stills + metadata from Shotdeck after logging in with your own account.

See README.md for setup and usage.
"""

import os, time, json, argparse, re
from pathlib import Path
from urllib.parse import urljoin
import pandas as pd
import requests
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, StaleElementReferenceException

# ---------- Config ----------
BASE = "https://shotdeck.com"
LOGIN_URL = f"{BASE}/welcome/login"

# Global counter for processed items
processed_shots = set()

def setup_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument("--window-size=1920,1080")
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """
    })
    return driver

def human_pause(a=0.3, b=0.9):
    time.sleep(a + (b-a)*0.5)

def selenium_login(driver, email, password):
    driver.get(LOGIN_URL)
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.NAME, "user")))
    driver.find_element(By.NAME, "user").send_keys(email)
    driver.find_element(By.NAME, "pass").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "form button[type=submit]").click()
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    human_pause()

def copy_cookies_to_requests(driver):
    session = requests.Session()
    for c in driver.get_cookies():
        session.cookies.set(c['name'], c['value'], domain=c.get('domain'))
    session.headers.update({
        "User-Agent": "Mozilla/5.0"
    })
    return session

def wait_for_gallery(driver, timeout=40, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            wait = WebDriverWait(driver, timeout)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#stills .outerimage")))
            print("Gallery loaded successfully")
            return True
        except TimeoutException:
            retries += 1
            print(f"Gallery loading timeout, retry {retries}/{max_retries}")
            if retries < max_retries:
                time.sleep(5)
                driver.refresh()
                time.sleep(3)
            else:
                raise TimeoutException(f"Failed to load gallery after {max_retries} retries")
    return False

def get_text(e):
    return re.sub(r"\s+", " ", e.text.strip())

def normalize_field_name(label):
    label = label.strip().rstrip(":")
    label = re.sub(r"[\s/]+", "_", label.lower())
    label = label.replace("-", "_")
    return label

def safe_click_element(driver, element, max_attempts=3):
    """Safely click an element with multiple fallback strategies"""
    for attempt in range(max_attempts):
        try:
            element.click()
            return True
        except (ElementClickInterceptedException, StaleElementReferenceException):
            print(f"Click issue, attempt {attempt + 1}/{max_attempts}")
            
            # Scroll element into center view with offset for headers
            driver.execute_script("""
                const element = arguments[0];
                const elementRect = element.getBoundingClientRect();
                const absoluteElementTop = elementRect.top + window.pageYOffset;
                const middle = absoluteElementTop - (window.innerHeight / 2) + (elementRect.height / 2);
                window.scrollTo(0, middle - 150);
            """, element)
            human_pause(0.5, 1.0)
            
            # Try JavaScript click
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                pass
                
            # Try ActionChains
            try:
                ActionChains(driver).move_to_element(element).click().perform()
                return True
            except Exception:
                pass
                
            human_pause(1, 2)
    
    return False

def open_shot_modal(driver, tile):
    """Open shot modal with robust click handling"""
    try:
        thumb = tile.find_element(By.CSS_SELECTOR, "a.gallerythumb")
        
        # Scroll to make element visible
        driver.execute_script("""
            const element = arguments[0];
            const elementRect = element.getBoundingClientRect();
            const absoluteElementTop = elementRect.top + window.pageYOffset;
            const middle = absoluteElementTop - (window.innerHeight / 2) + (elementRect.height / 2);
            window.scrollTo(0, middle - 150);
        """, thumb)
        
        human_pause(0.5, 1.0)
        
        if not safe_click_element(driver, thumb):
            raise ElementClickInterceptedException("All click attempts failed")
        
        # Wait for modal
        wait = WebDriverWait(driver, 15)
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "#shot-details-body")))
        human_pause(0.5, 1.0)
        
    except Exception as e:
        print(f"Failed to open modal: {e}")
        raise

def close_modal(driver):
    try:
        close_btn = driver.find_element(By.CSS_SELECTOR, ".modal-header button.close")
        close_btn.click()
        WebDriverWait(driver, 10).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, "#shot-details-body"))
        )
    except Exception:
        ActionChains(driver).send_keys("\ue00c").perform()
        time.sleep(0.5)

def parse_modal(driver):
    data = {}
    wait = WebDriverWait(driver, 15)
    
    try:
        title_hdr = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "#shotModalTitle")))
        data["title_year_raw"] = get_text(title_hdr)
    except Exception:
        data["title_year_raw"] = ""

    try:
        swatches = driver.find_elements(By.CSS_SELECTOR, ".palette a[style*='background-color']")
        data["palette_hex"] = ",".join([re.search(r'background-color:\s*([^;]+);?', a.get_attribute("style")).group(1)
                                        for a in swatches if a.get_attribute("style")])
    except Exception:
        data["palette_hex"] = ""

    groups = driver.find_elements(By.CSS_SELECTOR, "#shot_details .detail-group")
    for g in groups:
        try:
            label = normalize_field_name(g.find_element(By.CSS_SELECTOR, ".detail-type").text)
            details_div = g.find_element(By.CSS_SELECTOR, ".details")
            full = None
            spans = details_div.find_elements(By.CSS_SELECTOR, "span.full_location, span.full_filming_location")
            if spans:
                full = get_text(spans[0])
            if full:
                value = full
            else:
                anchors = details_div.find_elements(By.TAG_NAME, "a")
                if anchors:
                    value = ", ".join([get_text(a) for a in anchors])
                else:
                    value = get_text(details_div)
            data[label] = value
        except Exception:
            continue

    try:
        a = driver.find_element(By.CSS_SELECTOR, "#hero a")
        img_url = a.get_attribute("href")
    except Exception:
        try:
            img = driver.find_element(By.CSS_SELECTOR, "#shot_details_hero")
            img_url = img.get_attribute("src")
        except Exception:
            img_url = ""
    data["image_url"] = img_url
    return data

def parse_tile_basic(tile):
    d = {}
    d["shot_id"] = tile.get_attribute("data-shotid") or ""
    d["titleyear"] = tile.get_attribute("data-titleyear") or ""
    d["shot_status"] = tile.get_attribute("data-shot-status") or ""
    d["title_content_status"] = tile.get_attribute("data-title-content-status") or ""
    try:
        title_el = tile.find_element(By.CSS_SELECTOR, ".moviedetails.topdetails .gallerytitle")
        d["grid_title_raw"] = get_text(title_el)
    except Exception:
        d["grid_title_raw"] = ""
    try:
        thumb = tile.find_element(By.CSS_SELECTOR, "a.gallerythumb img.still")
        d["thumb_src"] = thumb.get_attribute("src")
    except Exception:
        d["thumb_src"] = ""
    try:
        d["data_filename"] = tile.find_element(By.CSS_SELECTOR, "a.gallerythumb").get_attribute("data-filename")
    except Exception:
        d["data_filename"] = ""
    return d

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def calculate_image_metadata(width, height):
    """Calculate image dimensions metadata with new column names"""
    if width <= 0 or height <= 0:
        return "", "", ""
    
    # Calculate greatest common divisor
    def gcd(a, b):
        while b:
            a, b = b, a % b
        return a
    
    # Get simplified ratio for image_aspect_ratio_fraction
    divisor = gcd(width, height)
    ratio_width = width // divisor
    ratio_height = height // divisor
    image_aspect_ratio_fraction = f"{ratio_width}:{ratio_height}"
    
    # Calculate normalized cinema aspect ratio for image_aspect_ratio_cinema
    ratio = width / height
    
    # Common cinema aspect ratios with tolerance
    cinema_standards = {
        2.39: "2.39:1",  # CinemaScope
        2.35: "2.35:1",  # Anamorphic
        1.85: "1.85:1",  # Flat
        1.78: "16:9",    # Widescreen TV
        1.66: "5:3",     # European widescreen
        1.33: "4:3",     # Academy ratio
        1.00: "1:1",     # Square
    }
    
    # Find closest standard ratio
    closest_ratio = min(cinema_standards.keys(), key=lambda x: abs(x - ratio))
    
    # Use if within reasonable tolerance (5%)
    if abs(ratio - closest_ratio) / closest_ratio < 0.05:
        image_aspect_ratio_cinema = cinema_standards[closest_ratio]
    else:
        # Round to 2 decimal places for non-standard ratios
        rounded_ratio = round(ratio, 2)
        image_aspect_ratio_cinema = f"{rounded_ratio:.2f}:1"
    
    return image_aspect_ratio_fraction, image_aspect_ratio_cinema

def save_image(session, url, out_dir: Path, shot_id: str, fallback_ext=".jpg"):
    if not url:
        return "", "", "", "", ""
    ensure_dir(out_dir)
    ext = os.path.splitext(url.split("?")[0])[1].lower() or fallback_ext
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        ext = fallback_ext
    out_path = out_dir / f"{shot_id}{ext}"
    try:
        r = session.get(url, timeout=60)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        
        # Get image dimensions using PIL if available
        try:
            from PIL import Image
            import io
            
            # Open image from bytes to avoid file I/O issues
            img = Image.open(io.BytesIO(r.content))
            image_width = str(img.width)
            image_height = str(img.height)
            
            # Calculate aspect ratios with new names
            image_aspect_ratio_fraction, image_aspect_ratio_cinema = calculate_image_metadata(img.width, img.height)
            
            return str(out_path), image_width, image_height, image_aspect_ratio_fraction, image_aspect_ratio_cinema
            
        except ImportError:
            print("PIL/Pillow not installed. Cannot calculate image dimensions.")
            return str(out_path), "", "", "", ""
        except Exception as e:
            print(f"Error calculating image dimensions: {e}")
            return str(out_path), "", "", "", ""
            
    except Exception:
        return "", "", "", "", ""

def incremental_scrape(driver, session, max_shots, img_dir, batch_size=20, scroll_pause=1.0):
    """Incrementally scrape items as we scroll down"""
    global processed_shots
    rows = []
    all_fields = set()
    last_height = driver.execute_script("return document.body.scrollHeight")
    processed_count = 0
    consecutive_no_new = 0
    max_consecutive_no_new = 3
    
    print(f"Starting incremental scrape for up to {max_shots} shots...")
    
    while processed_count < max_shots:
        # Get current visible tiles
        try:
            tiles = driver.find_elements(By.CSS_SELECTOR, "#stills .outerimage")
            current_tile_count = len(tiles)
            print(f"Visible tiles: {current_tile_count}, Processed: {processed_count}/{max_shots}")
            
            # Process tiles in batches
            new_tiles_processed = 0
            for tile in tiles:
                if processed_count >= max_shots:
                    break
                    
                try:
                    basic_info = parse_tile_basic(tile)
                    shot_id = basic_info.get("shot_id")
                    
                    # Skip if already processed or no shot_id
                    if not shot_id or shot_id in processed_shots:
                        continue
                    
                    print(f"Processing shot {processed_count + 1}/{max_shots} (ID: {shot_id})")
                    
                    # Process this tile
                    open_shot_modal(driver, tile)
                    details = parse_modal(driver)
                    close_modal(driver)
                    
                    # Download image and get dimensions
                    image_path, image_width, image_height, image_aspect_ratio_fraction, image_aspect_ratio_cinema = save_image(
                        session, details.get("image_url", ""), img_dir, shot_id
                    )
                    
                    # Create record with NEW column names (not modifying existing ones)
                    record = {
                        **basic_info, 
                        **details, 
                        "image_path": image_path,
                        # New columns with different names
                        "image_width": image_width,
                        "image_height": image_height,
                        "image_aspect_ratio_fraction": image_aspect_ratio_fraction,
                        "image_aspect_ratio_cinema": image_aspect_ratio_cinema
                    }
                    rows.append(record)
                    all_fields.update(record.keys())
                    
                    # Mark as processed
                    processed_shots.add(shot_id)
                    processed_count += 1
                    new_tiles_processed += 1
                    
                    # Save progress periodically
                    if processed_count % batch_size == 0:
                        print(f"Processed {processed_count} shots, saving intermediate progress...")
                        save_progress(rows, all_fields, str(base_output_dir / "shotdeck_progress_temp.xlsx"))
                    
                    human_pause(0.3, 0.7)
                    
                except Exception as e:
                    print(f"Error processing tile: {e}")
                    continue
            
            # Check if we found new tiles
            if new_tiles_processed == 0:
                consecutive_no_new += 1
                print(f"No new tiles found ({consecutive_no_new}/{max_consecutive_no_new})")
            else:
                consecutive_no_new = 0
            
            # Stop if no new tiles for a while or reached max
            if consecutive_no_new >= max_consecutive_no_new or processed_count >= max_shots:
                break
            
            # Scroll down to load more content
            print("Scrolling to load more content...")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            human_pause(scroll_pause, scroll_pause * 2)
            
            # Wait for new content to load
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                print("No more content to load")
                break
            last_height = new_height
            
        except Exception as e:
            print(f"Error during incremental scrape: {e}")
            break
    
    return rows, all_fields

def save_progress(rows, all_fields, filename):
    """Save intermediate progress"""
    if not rows:
        return
        
    # Existing columns remain the same, new columns added at the end
    cols = ["shot_id", "grid_title_raw", "titleyear", "title_year_raw",
            "shot_status", "title_content_status",
            "thumb_src", "data_filename", "image_url", "image_path"]
    
    # Add new columns with different names
    new_cols = ["image_width", "image_height", "image_aspect_ratio_fraction", "image_aspect_ratio_cinema"]
    cols = cols + new_cols
    
    # Add dynamic columns from modal parsing (which may include existing aspect_ratio column)
    dynamic = sorted([c for c in all_fields if c not in cols])
    cols = cols + dynamic

    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    for c in df.columns:
        df[c] = df[c].astype(str).str.replace(r"\s+\n\s+|\n", " ", regex=True).str.strip()

    df.to_excel(filename, index=False)
    print(f"Progress saved: {len(df)} rows to {filename}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-shots", type=int, default=100, help="How many shots to scrape")
    ap.add_argument("--out-xlsx", type=str, default="shotdeck_center_composition.xlsx")
    ap.add_argument("--images-dir", type=str, default="shotdeck_images")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--timeout", type=int, default=60, help="Timeout for page loading in seconds")
    ap.add_argument("--retries", type=int, default=3, help="Number of retries for loading")
    ap.add_argument("--batch-size", type=int, default=50, help="Save progress every X items")
    ap.add_argument("--scroll-pause", type=float, default=2.0, help="Pause between scrolls")
    args = ap.parse_args()

    load_dotenv()
    email = os.getenv("SHOTDECK_EMAIL")
    password = os.getenv("SHOTDECK_PASSWORD")
    browse_url = os.getenv("SHOTDECK_BROWSE_URL")
    output_base_dir = os.getenv("SHOTDECK_OUTPUT_DIR")
    
    if not email or not password:
        raise SystemExit("Please set SHOTDECK_EMAIL and SHOTDECK_PASSWORD in a .env file")
    
    if not browse_url:
        # Default browse URL if not specified in .env
        browse_url = "https://shotdeck.com/browse/stills"
        print(f"SHOTDECK_BROWSE_URL not set in .env, using default: {browse_url}")
    
    # Set up base output directory
    if output_base_dir:
        base_output_dir = Path(output_base_dir)
        ensure_dir(base_output_dir)
        print(f"Using base output directory: {base_output_dir}")
    else:
        base_output_dir = Path.cwd()
        print(f"SHOTDECK_OUTPUT_DIR not set in .env, using current directory: {base_output_dir}")

    driver = setup_driver(headless=args.headless)
    try:
        print("Logging in...")
        selenium_login(driver, email, password)
        
        print(f"Navigating to browse page: {browse_url}")
        driver.get(browse_url)
        
        WebDriverWait(driver, args.timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        
        print("Waiting for gallery to load...")
        wait_for_gallery(driver, timeout=args.timeout, max_retries=args.retries)

        session = copy_cookies_to_requests(driver)
        
        # Create full paths relative to base output directory
        img_dir = base_output_dir / args.images_dir
        out_xlsx = base_output_dir / args.out_xlsx
        
        print(f"Images will be saved to: {img_dir}")
        print(f"Excel file will be saved to: {out_xlsx}")

        # Incremental scraping
        rows, all_fields = incremental_scrape(
            driver, session, args.max_shots, img_dir, 
            batch_size=args.batch_size, scroll_pause=args.scroll_pause
        )

        # Final save
        save_progress(rows, all_fields, str(out_xlsx))
        print(f"Final results: Saved {len(rows)} rows to {out_xlsx}")
        
    except Exception as e:
        print(f"Error occurred: {e}")
        # Try to save progress even if error occurs
        if 'rows' in locals() and 'all_fields' in locals():
            error_backup = base_output_dir / f"shotdeck_error_backup.xlsx"
            save_progress(rows, all_fields, str(error_backup))
            print(f"Error backup saved to: {error_backup}")
        raise
    finally:
        driver.quit()
