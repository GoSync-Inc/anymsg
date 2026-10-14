# Synthetic OCR regression fixture

This 51 KiB image-only PDF was generated for this test. It contains no personal, customer, account or production data. Its page has an invoice heading, three language paragraphs and a three-column ruled table. The source was rendered at 150 dpi; the test uses the production bounded 1600 px raster path.

Expected table rows: `Alpha 2 1250.00`, `Beta 1 375.00`, `TOTAL 3 1625.00`.

It records a real extraction regression: Tesseract eng+rus with page segmentation 6 retained the paragraphs but omitted the entire table. Page segmentation 3 retained all table cells on three repeated isolated Linux runs. The fixture is checked in so tests need no ReportLab, model download, network access or installed source font.

SHA256: 8906a2cdf2b32867db01b2708fb704699cd9a87413f1943ec46071e01b305302
