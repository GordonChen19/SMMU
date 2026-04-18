from moviepy.video.io.VideoFileClip import VideoFileClip
import subprocess
import whisper 

model = whisper.load_model("base")


def trim_video(input_path, output_path, end_time):
    with VideoFileClip(input_path) as video:
        if hasattr(video, "subclipped"):
            trimmed_video = video.subclipped(0, end_time)
        else:
            trimmed_video = video.subclip(0, end_time)
        try:
            trimmed_video.write_videofile(output_path, codec="libx264", audio_codec="aac")
        finally:
            trimmed_video.close()


def mp4_to_mp3(input_path, output_path):
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path,
    ]
    subprocess.run(cmd, check=True)
    print(f"Converted {input_path} to {output_path}")


def extract_dialogue(audio_path):
    
    result = model.transcribe(audio_path)
    return result['text']
