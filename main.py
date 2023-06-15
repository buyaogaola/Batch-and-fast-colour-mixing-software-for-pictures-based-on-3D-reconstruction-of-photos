import os, array
import shutil, glob
import multiprocessing
import subprocess
import sys, time
import tkinter as tk
import tkinter.filedialog
import threading

# CC
import colour
from PIL import Image, ImageTk
from colour_checker_detection import detect_colour_checkers_segmentation
import imageio, rawpy
import PythonMagick

D65 = colour.CCS_ILLUMINANTS['CIE 1931 2 Degree Standard Observer']['D65']
REFERENCE_COLOUR_CHECKER = colour.CCS_COLOURCHECKERS['ColorChecker24 - After November 2014']#从colour库获取标准色卡值
REFERENCE_SWATCHES = colour.XYZ_to_RGB( colour.xyY_to_XYZ(list(REFERENCE_COLOUR_CHECKER.data.values())),
                                        REFERENCE_COLOUR_CHECKER.illuminant, D65,
                                        colour.RGB_COLOURSPACES['sRGB'].matrix_XYZ_to_RGB)#将srgb颜色值保存
exiftoolPath = "exiftool.exe -m -overwrite_original_in_place -tagsFromFile"#忽略小错误和警告运行，通过复制tmp文件覆盖原始文件

# Global variables
ColorChecker = ""
PhotosDir = ""
OutPutDir = ""

# ProcessNum = 4
status = ""
bBusy = False
PhotoNum = 0


def getColorCardCorrectionSwatches(colorChecker, cacheFolder):
    # 默认不使用增亮
    bNoAutoBrighten = True

    if not os.path.exists(colorChecker):
        return False, False, []

    # 对色卡的格式进行检测
    colorCheckerTiff = colorchangetoTiff(colorChecker, cacheFolder)
    # 对有色卡的照片进行模糊操作， 这样能去点部分噪点达到更好的检测
    print("blurring " + colorCheckerTiff)
    blurtiff = os.path.join(cacheFolder, os.path.splitext(os.path.basename(colorChecker))[0] + "_blur" + '.tiff')
    img = PythonMagick.Image(colorCheckerTiff)
    img.blur(0, 10)
    img.write(blurtiff)

    # 从给定的图像中检索颜色校正色板的值
    print(f"Detecting color checker in {blurtiff}")
    # 将图片中的非线性R'G'B'值转换成线性的RGB值
    image = colour.cctf_decoding(colour.io.read_image(blurtiff))
    # 检测图片中的色卡值
    swatches = detect_colour_checkers_segmentation(image)
    if len(swatches) < 1:
        return False, False, []
    # 对每个检测到的色卡值进行校色，并返回结果最准确的那个。
    Vresult, swatch = VerifyColorCardSwatches(swatches)

    return Vresult, bNoAutoBrighten, swatch


def VerifyColorCardSwatches(swatches):
    deviation = []
    # 标准色卡颜色值
    rgb_RCCL = colour.XYZ_to_RGB(colour.xyY_to_XYZ(list(REFERENCE_COLOUR_CHECKER.data.values())),
                                 D65, D65, colour.RGB_COLOURSPACES['sRGB'].matrix_XYZ_to_RGB)
    for swatch in swatches:
        swatch_cc = colour.colour_correction(swatch, rgb_RCCL, REFERENCE_SWATCHES)
        CCL = swatch_cc
        totalsum = 0.0
        for i in range(len(rgb_RCCL)):
            # 插值和
            totalsum += abs(rgb_RCCL[i][0] - CCL[i][0]) + abs(rgb_RCCL[i][1] - CCL[i][1]) + abs(
                rgb_RCCL[i][2] - CCL[i][2])
        deviation.append(totalsum)
    print("swatches deviations: " + str(deviation))
    min_d = 100.0
    min_i = -1
    for i in range(len(deviation)):
        if deviation[i] < min_d:
            min_d = deviation[i]
            min_i = i
    print("The lowest devi is: " + str(min_d))
    result = False
    # 通常插值和在8.0一下都可以接受
    if min_d < 3.0:
        print("devi is good ! ")
        result = True
    elif min_d < 5.0:
        print("devi is near average ! ")
        result = True
    elif min_d < 8.0:
        print("devi is near BAD ! the COLORS will be off!!!!")
        result = True
    else:
        print("devi is BAD ! the Swatches are completely off!!!!")
        result = True
    return result, swatches[min_i]


def CCProcess(files, Vresult, swatch, colorCorrectFolder, CacheDir):
    # folder = folderpath.split("\\")[-1]
    # LocalCcCache = os.path.join(LocalCachPath, folder, "Cache")
    for file in files:
        # 将图片转换成tiff文件格式方便校色
        tifffile = colorchangetoTiff(file, CacheDir)
        print("ColorCorrecting: " + tifffile)
        # 将图片中的非线性R'G'B'值转换成线性的RGB值
        image = colour.cctf_decoding(colour.io.read_image(os.path.join(CacheDir, tifffile)))
        # 使用拍摄颜色值（swatch）进行校色
        cc_image = colour.colour_correction(image, swatch, REFERENCE_SWATCHES, 'Finlayson 2015')
        # 这里将校色完成的图片存为32位图像
        tiff_CC_32 = os.path.join(CacheDir, os.path.splitext(os.path.basename(file))[0] + "_CC" + '.tiff')
        # 将图片中的线性的RGB值值转换成非线性R'G'B'
        colour.io.write_image(colour.cctf_encoding(cc_image), tiff_CC_32)  # write out 32bit image
        print("Save CC image to jpg: " + file)
        cctifffile = os.path.join(CacheDir, os.path.splitext(os.path.basename(file))[0] + '.tiff')
        # ccpngfile = os.path.join(folderpath,'ColorCorrected', os.path.splitext(os.path.basename(file))[0] + '.tiff')
        # 转换为8位图片以节约空间
        img = PythonMagick.Image(tiff_CC_32)
        img.depth(8)
        img.write(cctifffile)

        # 项目要求转换为JPG并缩放尺寸
        Final_ccJpgfile = os.path.join(colorCorrectFolder, os.path.splitext(os.path.basename(file))[0] + '.jpg')
        img = Image.open(cctifffile)
        img.save(Final_ccJpgfile, quality=100, subsampling=0)

        TransferMetaData(file, Final_ccJpgfile)


def colorchangetoTiff(file, outfolder, bNo_Auto_Bright=True):

    tiffFile = ""
    extention = file.rsplit(".", 1)[-1]
    tiffFile = os.path.join(outfolder, os.path.splitext(os.path.basename(file))[0] + '.tiff')
    # 对于格式做一个简单的转换
    if (extention == "jpg" or extention == "JPG" or
            extention == "png" or extention == "PNG" or
            extention == "tga" or extention == "TGA"):
        im = imageio.imread(file)
        imageio.imsave(tiffFile, im)
        return tiffFile

def TransferMetaData(SourceFile, DistFile):
    command = exiftoolPath
    command += " " + SourceFile
    command += " " + DistFile
    print(command)
    os.system(command)


def CleanUP(CacheDir):
    if os.path.exists(CacheDir):
        shutil.rmtree(CacheDir)


def MainCCProcess(ColorChecker, folderpath, OutPutDir, Threads):
    DisableButtons()
    Format = ColorChecker.rsplit(".", 1)[-1]
    FileList = glob.glob(os.path.join(folderpath, '*.' + Format))

    if len(FileList) < 2:
        print("No file to CC!")
        return False

    CCDir = OutPutDir
    CleanUP(CCDir)
    os.makedirs(CCDir, exist_ok=True)
    CacheDir = os.path.join(CCDir, "Cache")
    os.makedirs(CacheDir, exist_ok=True)
    global PhotoNum
    PhotoNum = len(FileList)
    print("Prepare ColorChecker")
    Vresult, bNoAutoBrighten, swatch = getColorCardCorrectionSwatches(ColorChecker, CacheDir)

    if Vresult:
        print("ColorCheckerDetected")
    else:
        print("ColorCheckerDetectionFailed, Abort")
        # CleanUp(CCDir)
        return False

    print("***Start Mass ColorCorrcting***")
    Process = []
    FramePerProcess = int(len(FileList) / int(Threads))

    for i in range(Threads + 1):
        LastFrame = min((i + 1) * FramePerProcess, len(FileList))
        files = FileList[i * FramePerProcess:LastFrame]
        x = multiprocessing.Process(target=CCProcess, args=(files, Vresult, swatch, CCDir, CacheDir,))
        Process.append(x)
        x.start()
        time.sleep(5)

    for ps in Process:
        ps.join()
    print(folderpath)
    shutil.rmtree(folderpath)
    EnableButtons()


def ColorCorrect():


    subprocess.run(
        ['python', r'C:\Users\admin\Desktop\new\yolov5-5.0\detect.py', r'--project=' + PhotosDir,
         r'--name=' + PhotosDir + '\exp', r'--source=' + PhotosDir])
    if ColorChecker == "" or PhotosDir == "" or OutPutDir == "":
        print("Invalid Settings!")
        return False
    str=PhotosDir+'\exp'
    BakeT = threading.Thread(target=MainCCProcess,
                             args=(ColorChecker, str, OutPutDir, int(ProcessNumBlock.get()),))
    BakeT.start()



# def detectit():
#     subprocess.run(
#         ['python', r'C:\Users\admin\Desktop\new\yolov5-5.0\detect.py', r'--project='+PhotosDir,
#          r'--name='+PhotosDir+'\exp', r'--source='+PhotosDir])


# UI stuff
def Choose_ColourChecker():
    global ColorChecker
    filename = tk.filedialog.askopenfilename()
    ColorCheckerBlock["text"] = filename
    ColorChecker = filename
    print(filename)


def Choose_PhotoDir():
    global PhotosDir
    filedir = tk.filedialog.askdirectory()
    PhotosDirBlock["text"] = filedir
    PhotosDir = filedir
    print(filedir)


def Choose_OutPutDir():
    global OutPutDir
    filedir = tk.filedialog.askdirectory()
    OutPutDirBlock["text"] = filedir
    OutPutDir = filedir
    print(filedir)


def DisableButtons():
    ChooseColorChecker["state"] = tk.DISABLED
    ChoosePhotosDir["state"] = tk.DISABLED
    ChooseOutputDir["state"] = tk.DISABLED
    StartButton["state"] = tk.DISABLED
    global bBusy
    bBusy = True


def EnableButtons():
    ChooseColorChecker["state"] = tk.NORMAL
    ChoosePhotosDir["state"] = tk.NORMAL
    ChooseOutputDir["state"] = tk.NORMAL
    StartButton["state"] = tk.NORMAL
    global bBusy, status
    bBusy = False
    status = "WaitingForCommand"


def GetProgress():
    global PhotoNum
    if PhotoNum == 0:
        return 0
    else:
        FileNum = len(glob.glob(os.path.join(OutPutDir, '*.jpg')))
        progress = int(float(FileNum) / float(PhotoNum) * 100.0)
        return progress


def get_image(filename,width,height):
    im = Image.open(filename).resize((width,height))
    return ImageTk.PhotoImage(im)



if __name__ == "__main__":
    multiprocessing.freeze_support()
    Window = tk.Tk()
    Window.title("ColourCorrector")
    Window.geometry("800x500")


    canvas_root=tk.Canvas(Window,width=800,height=600)
    im_root=get_image(r'C:\Users\admin\Desktop\view.jpg',800,600)
    canvas_root.create_image(400,300,image=im_root)
    canvas_root.grid()

    ColorCheckerL = tk.Label(Window, text="ColourChecker(色卡路径)： ", width=25, height=1)
    ColorCheckerL_window=canvas_root.create_window(100,30,window=ColorCheckerL)
    ColorCheckerBlock = tk.Label(Window, text="None(未指定色卡)", width=50, height=1)
    ColorCheckerBlock_window = canvas_root.create_window(450, 30, window=ColorCheckerBlock)
    ChooseColorChecker = tk.Button(Window, text="...", width=4, height=1, command=Choose_ColourChecker)
    ChooseColorChecker_window = canvas_root.create_window(750, 30, window=ChooseColorChecker)

    PhotosDirL = tk.Label(Window, text="Photos(待较色路径)： ", width=25, height=1)
    PhotosDirL_window = canvas_root.create_window(100, 90, window=PhotosDirL)
    PhotosDirBlock = tk.Label(Window, text="None(未指定路径)", width=50, height=1)
    PhotosDirBlock_window = canvas_root.create_window(450, 90, window=PhotosDirBlock)
    ChoosePhotosDir = tk.Button(Window, text="...", width=4, height=1, command=Choose_PhotoDir)  #
    ChoosePhotosDir_window = canvas_root.create_window(750, 90, window=ChoosePhotosDir)

    OutPutDirL = tk.Label(Window, text="OutPut(输出路径)： ", width=25, height=1)
    OutPutDirL_window = canvas_root.create_window(100, 150, window=OutPutDirL)
    OutPutDirBlock = tk.Label(Window, text="None(未指定输出)", width=50, height=1)
    OutPutDirBlock_window = canvas_root.create_window(450, 150, window=OutPutDirBlock)
    ChooseOutputDir = tk.Button(Window, text="...", width=4, height=1, command=Choose_OutPutDir)
    ChooseOutputDir_window = canvas_root.create_window(750, 150, window=ChooseOutputDir)

    ProcessNumBlock = tk.Entry(Window, width=15)
    ProcessNumBlock.insert(0, "3")

    StartButton = tk.Button(Window, text="Start ColorCorrection(开始较色)", width=30, height=1, command=ColorCorrect)
    StartButton_window = canvas_root.create_window(650, 350, window=StartButton)

    l = tk.Label(Window, text="ABC", width=80, height=1, font=("Arial"))
    l.place(x=5, y=250)



    # ColorCheckerL = tk.Label(Window, text="ColourChecker(色卡路径)： ", width=25, height=1)
    # ColorCheckerL.grid(row=0, column=0)
    # ColorCheckerBlock = tk.Label(Window, text="None(未指定色卡)", width=50, height=1)
    # ColorCheckerBlock.grid(row=0, column=1)
    # ChooseColorChecker = tk.Button(Window, text="...", width=4, height=1, command=Choose_ColourChecker)
    # ChooseColorChecker.grid(row=0, column=2, sticky="E")
    #
    # PhotosDirL = tk.Label(Window, text="Photos(待较色路径)： ", width=25, height=1)
    # PhotosDirL.grid(row=1, column=0)
    # PhotosDirBlock = tk.Label(Window, text="None(未指定路径)", width=50, height=1)
    # PhotosDirBlock.grid(row=1, column=1)
    # ChoosePhotosDir = tk.Button(Window, text="...", width=4, height=1, command=Choose_PhotoDir)  #
    # ChoosePhotosDir.grid(row=1, column=2, sticky="E")
    #
    # OutPutDirL = tk.Label(Window, text="OutPut(输出路径)： ", width=25, height=1)
    # OutPutDirL.grid(row=2, column=0)
    # OutPutDirBlock = tk.Label(Window, text="None(未指定输出)", width=50, height=1)
    # OutPutDirBlock.grid(row=2, column=1)
    # ChooseOutputDir = tk.Button(Window, text="...", width=4, height=1, command=Choose_OutPutDir)
    # ChooseOutputDir.grid(row=2, column=2, sticky="E")
    #
    # ProcessNumBlock = tk.Entry(Window, width=15)
    # ProcessNumBlock.insert(0, "3")
    #
    # StartButton = tk.Button(Window, text="Start ColorCorrection(开始较色)", width=30, height=1, command=ColorCorrect)
    # StartButton.grid(row=3, column=2, sticky="W")
    #
    #
    # l = tk.Label(Window, text="ABC", width=80, height=1, font=("Arial"))
    # l.place(x=5, y=125)


    def UpdateStatus():
        global status, bBusy
        percent = GetProgress()
        status = str(percent) + "%"
        l["text"] = status
        l.after(500, UpdateStatus)


    l.after(500, UpdateStatus)

    Window.mainloop()