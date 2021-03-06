import os
import torch
import glob
import argparse
import numpy as np
import scipy.misc as misc


from ptsemseg.models import get_model
from ptsemseg.loader import get_loader
from ptsemseg.utils import convert_state_dict

try:
    import pydensecrf.densecrf as dcrf
except:
    print(
        "Failed to import pydensecrf,\
           CRF post-processing will not work"
    )


def test(args, img_path, device, loader, model):
    # Setup image
    print("Read Input Image from : {}".format(args.folder_path))
    img = misc.imread(img_path)

    resized_img = misc.imresize(img, (loader.img_size[0], loader.img_size[1]), interp="bicubic")

    orig_size = img.shape[:-1]
    if model_name in ["pspnet", "icnet", "icnetBN"]:
        # uint8 with RGB mode, resize width and height which are odd numbers
        img = misc.imresize(img, (orig_size[0] // 32 * 32 + 1, orig_size[1] // 32 * 32 + 1))
    else:
        img = misc.imresize(img, (loader.img_size[0], loader.img_size[1]))

    img = img[:, :, ::-1]
    img = img.astype(np.float64)
    img -= loader.mean
    if args.img_norm:
        img = img.astype(float) / 255.0

    # NHWC -> NCHW
    img = img.transpose(2, 0, 1)
    img = np.expand_dims(img, 0)
    img = torch.from_numpy(img).float()

    images = img.to(device)
    outputs = model(images)

    if args.dcrf:
        unary = outputs.data.cpu().numpy()
        unary = np.squeeze(unary, 0)
        unary = -np.log(unary)
        unary = unary.transpose(2, 1, 0)
        w, h, c = unary.shape
        unary = unary.transpose(2, 0, 1).reshape(loader.n_classes, -1)
        unary = np.ascontiguousarray(unary)

        resized_img = np.ascontiguousarray(resized_img)

        d = dcrf.DenseCRF2D(w, h, loader.n_classes)
        d.setUnaryEnergy(unary)
        d.addPairwiseBilateral(sxy=5, srgb=3, rgbim=resized_img, compat=1)

        q = d.inference(50)
        mask = np.argmax(q, axis=0).reshape(w, h).transpose(1, 0)
        decoded_crf = loader.decode_segmap(np.array(mask, dtype=np.uint8))
        dcrf_path = args.out_path[:-4] + "_drf.png"
        misc.imsave(dcrf_path, decoded_crf)
        print("Dense CRF Processed Mask Saved at: {}".format(dcrf_path))

    pred = np.squeeze(outputs.data.max(1)[1].cpu().numpy(), axis=0)
    if model_name in ["pspnet", "icnet", "icnetBN"]:
        pred = pred.astype(np.float32)
        # float32 with F mode, resize back to orig_size
        pred = misc.imresize(pred, orig_size, "nearest", mode="F")

    decoded = loader.decode_segmap(pred)
    print("Classes found: ", np.unique(pred))
    filename = img_path.split('/')[-1].split('.')[0]
    misc.imsave('output/{}output.png'.format(filename), decoded)
    # misc.imsave(args.out_path, decoded)
    print("Segmentation Mask Saved at: {}".format(args.out_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Params")
    parser.add_argument(
        "--model_path",
        type=str,
        default="runs/icnet_sunrgbd./36340/icnetBN_sunrgbd_best_model.pkl",
        help="Path to the saved model",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="sunrgbd",
        help="Dataset to use ['pascal, camvid, ade20k etc']",
    )

    parser.add_argument(
        "--img_norm",
        dest="img_norm",
        action="store_true",
        help="Enable input image scales normalization [0, 1] \
                              | True by default",
    )
    parser.add_argument(
        "--no-img_norm",
        dest="img_norm",
        action="store_false",
        help="Disable input image scales normalization [0, 1] |\
                              True by default",
    )
    parser.set_defaults(img_norm=True)

    parser.add_argument(
        "--dcrf",
        dest="dcrf",
        action="store_true",
        help="Enable DenseCRF based post-processing | \
                              False by default",
    )
    parser.add_argument(
        "--no-dcrf",
        dest="dcrf",
        action="store_false",
        help="Disable DenseCRF based post-processing | \
                              False by default",
    )
    parser.set_defaults(dcrf=False)

    parser.add_argument(
        "--folder_path", nargs="?", type=str, default='../data/samples', help="Path of the input image"
    )
    parser.add_argument(
        "--out_path", nargs="?", type=str, default='output', help="Path of the output segmap"
    )
    opt = parser.parse_args()

    model_file_name = os.path.split(opt.model_path)[1]
    model_name = model_file_name[: model_file_name.find("_")]

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    data_loader = get_loader(opt.dataset)
    img_loader = data_loader(root=None, is_transform=True, img_norm=opt.img_norm, test_mode=True)
    n_classes = img_loader.n_classes

    # Setup Model
    model_dict = {"arch": model_name, "is_batchnorm": True}
    model = get_model(model_dict, n_classes, version=opt.dataset)
    state = convert_state_dict(torch.load(opt.model_path)["model_state"])
    model.load_state_dict(state)
    model.eval()
    model.to(dev)

    files = sorted(glob.glob("%s/*.*" % opt.folder_path))
    for image_path in files:
        test(opt, image_path, dev, img_loader, model)
