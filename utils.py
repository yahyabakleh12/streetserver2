# utils.py

import cv2
import numpy as np


def _avg_hash(gray: np.ndarray, hash_size: int = 8) -> np.ndarray:
    """Return average hash of image as boolean array."""
    img = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    avg = img.mean()
    return (img > avg).astype(np.uint8)


def _hash_diff(h1: np.ndarray, h2: np.ndarray) -> int:
    """Compute Hamming distance between two binary hashes."""
    return int(np.count_nonzero(h1 != h2))


def is_same_image(
    img_path1: str,
    img_path2: str,
    *,
    camera_id: int | None = None,
    spot_number: int | None = None,
    min_match_count: int = 50,
    inlier_ratio_thresh: float = 0.5,
) -> bool:
    """
    Compare two images using SIFT feature matching + RANSAC-based homography.
    Returns True if they are deemed the “same” object (e.g. same parked car),
    False otherwise.

    Steps:
      1) Load both images in grayscale.
      2) Resize them to a reasonable maximum size.
      3) Quickly compare using an average hash; if hashes are almost equal,
         consider the images identical.
      4) Detect SIFT keypoints/descriptors.
      5) Use FLANN to find KNN matches (k=2) and apply Lowe’s ratio test.
      6) If #good_matches < min_match_count, immediately return False.
      7) Run cv2.findHomography with RANSAC on the matched keypoints.
      8) Count inliers (mask returned by findHomography). If
         inliers / good_matches ≥ inlier_ratio_thresh, return True.
         Otherwise return False.

    Arguments:
      img_path1, img_path2 : filepaths to the two images to compare.
      camera_id, spot_number : optional identifiers for a parking spot. If
        provided, the bounding box will be looked up in the database and both
        images will be cropped prior to comparison.
      min_match_count      : minimum number of “good matches” before attempting
        homography. Defaults to 50.
      inlier_ratio_thresh  : fraction of inliers vs. good_matches to consider
        “same” (e.g. 0.5 = 50%). Defaults to 0.5.

    Returns:
      True  if images are “same” under these criteria,
      False otherwise.
    """
    # return False
    # 1) Load grayscale
    img1 = cv2.imread(img_path1, cv2.IMREAD_GRAYSCALE)
    img2 = cv2.imread(img_path2, cv2.IMREAD_GRAYSCALE)
    if img1 is None or img2 is None:
        # If we can’t load, treat as different
        return False

    # 1a) Optionally resize very large images down to a max dimension (e.g. 800px)
    def resize_max(img, max_dim=800):
        h, w = img.shape[:2]
        if max(h, w) <= max_dim:
            return img
        scale = max_dim / float(max(h, w))
        return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    img1 = resize_max(img1, 800)
    img2 = resize_max(img2, 800)

    # Optional crop based on parking spot bbox looked up from DB
    if camera_id is not None and spot_number is not None:
        try:
            from db import SessionLocal
            from models import Spot

            db = SessionLocal()
            spot = (
                db.query(Spot)
                .filter_by(camera_id=camera_id, spot_number=spot_number)
                .first()
            )
        finally:
            try:
                db.close()
            except Exception:
                pass

        if spot:
            x1, y1, x2, y2 = int(spot.bbox_x1), int(spot.bbox_y1), int(spot.bbox_x2), int(spot.bbox_y2)

            h, w = img1.shape[:2]
            x1a, x2a = max(0, min(x1, w)), max(0, min(x2, w))
            y1a, y2a = max(0, min(y1, h)), max(0, min(y2, h))
            if x2a > x1a and y2a > y1a:
                img1 = img1[y1a:y2a, x1a:x2a]

            h2, w2 = img2.shape[:2]
            x1b, x2b = max(0, min(x1, w2)), max(0, min(x2, w2))
            y1b, y2b = max(0, min(y1, h2)), max(0, min(y2, h2))
            if x2b > x1b and y2b > y1b:
                img2 = img2[y1b:y2b, x1b:x2b]

    # Early checks using simple hashing. Identical arrays or nearly identical
    # average hashes indicate the images are the same without running the more
    # expensive feature matcher.
    if img1.shape == img2.shape:
        if np.array_equal(img1, img2):
            return True
        h1 = _avg_hash(img1)
        h2 = _avg_hash(img2)
        if _hash_diff(h1, h2) <= 5:
            return True

    # 2) Detect SIFT keypoints and descriptors
    sift = cv2.SIFT_create()
    kp1, des1 = sift.detectAndCompute(img1, None)
    kp2, des2 = sift.detectAndCompute(img2, None)
    if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
        # Not enough features: treat as different
        return False

    # 3) FLANN-based matcher setup
    FLANN_INDEX_KDTREE = 1
    index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
    search_params = dict(checks=50)
    flann = cv2.FlannBasedMatcher(index_params, search_params)

    # 4) KNN match (k=2) + Lowe's ratio test
    matches = flann.knnMatch(des1, des2, k=2)
    good_matches = []
    for m, n in matches:
        if m.distance < 0.7 * n.distance:
            good_matches.append(m)

    # 5) If too few good matches, return False
    if len(good_matches) < min_match_count:
        return False

    # 6) Prepare points for findHomography
    src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

    # 7) Compute homography with RANSAC
    M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if mask is None:
        return False

    inliers = mask.ravel().tolist().count(1)
    inlier_ratio = inliers / float(len(good_matches))

    # 8) Decide “same” if inlier ratio exceeds threshold
    return inlier_ratio >= inlier_ratio_thresh
