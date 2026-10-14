"""Train the Day-0 biomedical waste classification model on the RTX 4060 GPU.

Uses ultralytics YOLO11n-cls, trains on the prepared cls_data folder,
evaluates top-1 accuracy, and exports to ONNX and NCNN.
"""

import sys
import time
from pathlib import Path
import torch
from ultralytics import YOLO

DATA_DIR = Path("bioseg/data/datasets/cls_data").resolve()
MODEL_OUT_DIR = Path("bioseg/edge/models").resolve()

def train_bioseg_classifier(epochs: int = 5, imgsz: int = 224, batch: int = 32):
    print(f"PyTorch Version: {torch.__version__}")
    device = "0" if torch.cuda.is_available() else "cpu"
    print(f"Training on device: {device} ({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'})")

    MODEL_OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load yolo11n-cls pretrained backbone
    model = YOLO("yolo11n-cls.pt")

    # Train classification model
    print(f"Starting training on {DATA_DIR} for {epochs} epochs...")
    t0 = time.time()
    results = model.train(
        data=str(DATA_DIR),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device,
        workers=4,
        project="bioseg_runs",
        name="day0_cls",
        exist_ok=True,
    )
    train_time = time.time() - t0
    print(f"Training completed in {train_time:.1f} seconds.")

    # Validate
    val_results = model.val()
    print(f"Validation Top-1 Accuracy: {val_results.top1:.4f}")
    print(f"Validation Top-5 Accuracy: {val_results.top5:.4f}")

    # Export to ONNX
    onnx_path = model.export(format="onnx", imgsz=imgsz)
    print(f"Exported to ONNX: {onnx_path}")

    # Copy to edge models directory
    best_pt = Path("bioseg_runs/day0_cls/weights/best.pt")
    if best_pt.exists():
        import shutil
        shutil.copy2(best_pt, MODEL_OUT_DIR / "yolo11n-bioseg.pt")
        print(f"Saved best weights to {MODEL_OUT_DIR / 'yolo11n-bioseg.pt'}")

    return results

if __name__ == "__main__":
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    train_bioseg_classifier(epochs=epochs)
