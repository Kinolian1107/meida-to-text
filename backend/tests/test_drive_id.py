from app.pipeline.google_drive import extract_drive_file_id


def test_extract_file_d():
    url = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrsTuvWxYz/view?usp=sharing"
    assert extract_drive_file_id(url) == "1AbCdEfGhIjKlMnOpQrsTuvWxYz"


def test_extract_open_id():
    url = "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrsTuvWxYz"
    assert extract_drive_file_id(url) == "1AbCdEfGhIjKlMnOpQrsTuvWxYz"
