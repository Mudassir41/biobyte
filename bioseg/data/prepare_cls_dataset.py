"""Prepare and split the biomedical waste dataset into YOLO classification format.

Classes mapped to routes:
- gauze -> YELLOW (soiled absorbent / dressing)
- glove -> RED (contaminated / recyclable latex and nitrile)
- test_tube -> RED (lab tube / infectious plastic)
- urine_bag -> RED (catheter / collection bag)
- syringe -> RED (plastic syringe body) / HOLD when needle present
- medical_glasses -> HOLD (glass veto)
"""

import os
import shutil
import random
from pathlib import Path
from PIL import Image

SRC_BASE = Path("bioseg/data/datasets/medical_waste/Medical Waste dataset/images")
OUT_BASE = Path("bioseg/data/datasets/cls_data")

# Class mapping rules
CLASS_MAPPING = {
    "gauze": ["gauze"],
    "glove": [
        "glove_pair_latex", "glove_pair_nitrile", "glove_pair_surgery",
        "glove_single_latex", "glove_single_nitrile", "glove_single_surgery",
    ],
    "test_tube": ["test_tube"],
    "urine_bag": ["urine_bag"],
    "medical_glasses": ["medical_glasses"],
    "syringe": ["syringe"],
}

VAL_SPLIT_RATIO = 0.20
RANDOM_SEED = 42

def prepare_dataset():
    random.seed(RANDOM_SEED)
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    train_dir = OUT_BASE / "train"
    val_dir = OUT_BASE / "val"
    train_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)

    summary = {}

    for target_class, src_folders in CLASS_MAPPING.items():
        (train_dir / target_class).mkdir(exist_ok=True)
        (val_dir / target_class).mkdir(exist_ok=True)

        valid_images = []
        for src_folder in src_folders:
            folder_path = SRC_BASE / src_folder
            if not folder_path.exists():
                continue
            for img_file in folder_path.iterdir():
                if img_file.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    try:
                        # Quick validation check: can PIL open it?
                        with Image.open(img_file) as im:
                            im.verify()
                        valid_images.append(img_file)
                    except Exception:
                        pass

        random.shuffle(valid_images)
        split_idx = int(len(valid_images) * (1 - VAL_SPLIT_RATIO))
        train_imgs = valid_images[:split_idx]
        val_imgs = valid_images[split_idx:]

        for img in train_imgs:
            dest = train_dir / target_class / f"{img.parent.name}_{img.name}"
            shutil.copy2(img, dest)

        for img in val_imgs:
            dest = val_dir / target_class / f"{img.parent.name}_{img.name}"
            shutil.copy2(img, dest)

        summary[target_class] = {
            "total": len(valid_images),
            "train": len(train_imgs),
            "val": len(val_imgs),
        }
        print(f"Class '{target_class}': {len(train_imgs)} train, {len(val_imgs)} val (total {len(valid_images)})")

    return summary

if __name__ == "__main__":
    prepare_dataset()
