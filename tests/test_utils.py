from shotdeck_scraper.scraper import calculate_image_metadata


def test_calculate_image_metadata_square():
    meta = calculate_image_metadata(1000, 1000)
    assert meta["image_width"] == 1000
    assert meta["image_height"] == 1000
    assert meta["image_aspect_ratio_fraction"] == "1:1"
