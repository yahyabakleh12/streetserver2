# utils.py

import cv2
import numpy as np


def is_same_image(
    img_path1: str,
    img_path2: str,
    min_match_count: int = 50,
    inlier_ratio_thresh: float = 0.5
) -> bool:
    """
    Compare two images using SIFT feature matching + RANSAC-based homography.
    Returns True if they are deemed the “same” object (e.g. same parked car),
    False otherwise.

    Steps:
      1) Load both images in grayscale.
      2) Detect SIFT keypoints/descriptors.
      3) Use FLANN to find KNN matches (k=2).
      4) Apply Lowe's ratio test to keep “good matches.”
      5) If #good_matches < min_match_count, immediately return False.
      6) Run cv2.findHomography with RANSAC on the matched keypoints.
      7) Count inliers (mask returned by findHomography). If
         inliers / good_matches ≥ inlier_ratio_thresh, return True.
         Otherwise return False.

    Arguments:
      img_path1, img_path2    : filepaths to the two images to compare.
      min_match_count         : minimum number of “good matches” before
                                attempting homography. Defaults to 50.
      inlier_ratio_thresh     : fraction of inliers vs. good_matches to
                                consider “same” (e.g. 0.5 = 50%). Defaults to 0.5.

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
