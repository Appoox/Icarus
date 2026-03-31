import cv2
import numpy as np
import os

# ---------- Parameters ----------
image_path = "('printed3', '.pdf')_page_0_line_6.png"   # Change to your input image
output_dir = "lines_output"
os.makedirs(output_dir, exist_ok=True)

# ---------- Projection-based line detection ----------
def detect_lines(img, min_height=20):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, threshed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Sum pixels along rows
    hist = np.sum(threshed, axis=1)

    lines = []
    start = None
    for i, val in enumerate(hist):
        if val > 0 and start is None:
            start = i
        elif val == 0 and start is not None:
            if i - start > min_height:   # Ignore very small boxes
                lines.append((start, i))
            start = None
    return lines

# ---------- Mouse callback for adjusting bounding boxes ----------
drawing = False
ix, iy = -1, -1
rect = None

def draw_rectangle(event, x, y, flags, param):
    global ix, iy, drawing, rect, clone

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        ix, iy = x, y
        rect = None

    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing:
            clone = param.copy()
            cv2.rectangle(clone, (ix, iy), (x, y), (0, 255, 0), 2)
            cv2.imshow("Adjust Line", clone)

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        rect = (ix, iy, x, y)
        cv2.rectangle(param, (ix, iy), (x, y), (0, 255, 0), 2)
        cv2.imshow("Adjust Line", param)

# ---------- Main ----------
if __name__ == "__main__":
    img = cv2.imread(image_path)
    if img is None:
        print("Error: Image not found!")
        exit()

    lines = detect_lines(img)
    print(f"Detected {len(lines)} lines")

    count = 1
    for (y1, y2) in lines:
        # Crop line region
        crop = img[y1:y2, :]

        # Show to user for adjustment
        clone = crop.copy()
        cv2.imshow("Adjust Line", clone)
        cv2.setMouseCallback("Adjust Line", draw_rectangle, clone)

        print("Draw box around line and press 's' to save, 'n' to skip, 'q' to quit")

        while True:
            key = cv2.waitKey(0) & 0xFF

            if key == ord("s"):
                if rect:
                    x1, y1, x2, y2 = rect
                    x1, x2 = sorted([x1, x2])
                    y1, y2 = sorted([y1, y2])
                    roi = crop[y1:y2, x1:x2]
                else:
                    roi = crop
                save_path = os.path.join(output_dir, f"line{count}.png")
                cv2.imwrite(save_path, roi)
                print(f"Saved {save_path}")
                count += 1
                break

            elif key == ord("n"):  # skip this line
                break

            elif key == ord("q"):  # quit
                cv2.destroyAllWindows()
                exit()

    cv2.destroyAllWindows()
    print("Done! Cropped lines saved in:", output_dir)
