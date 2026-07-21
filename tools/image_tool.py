import os
import time
import mimetypes
from PIL import Image
from io import BytesIO
from google import genai
from google.genai import types


class ImageTools:
    def __init__(self, config: dict):
        self.output_dir = config.get("image_generation", {}).get("output_folder", "output/images")
        os.makedirs(self.output_dir, exist_ok=True)
        
        project_id = config.get("gcloud", {}).get("project_id", os.getenv("GOOGLE_CLOUD_PROJECT"))
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        
        self.client = genai.Client(vertexai=True, project=project_id, location=location)

        self.on_show_image = None
        self.last_create_time = 0.0
        self.last_show_time = 0.0
        self.cooldown_duration = float(config.get("image_generation", {}).get("cooldown_duration", 60.0))

        def create_image(image_prompt: str, metadata_description: str, reference_image: str | None = None) -> str:
            """Generates an image from a prompt.

            Args:
                image_prompt: The prompt describing the image to generate.
                metadata_description: A description to embed as metadata in the image.
                reference_image: Optional file path to a reference image.

            Returns:
                A string indicating the file path of the saved generated image, or an error message.
            """
            try:
                now = time.time()
                elapsed = now - self.last_create_time
                if elapsed < self.cooldown_duration:
                    remaining = int(self.cooldown_duration - elapsed)
                    return f"Error: create_image is on cooldown. Please wait {remaining} more seconds before generating another image."

                prompt = image_prompt
                print(f"[create_image tool] Generating image from prompt: {prompt[:100]}...")
                
                response = self.client.models.generate_images(
                    model="imagen-3.0-generate-002",
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1
                    )
                )
                
                saved_paths = []
                if getattr(response, "generated_images", None):
                    for i, generated_image in enumerate(response.generated_images):
                        if getattr(generated_image, "image", None) is not None:
                             image_bytes = generated_image.image.image_bytes
                             image = Image.open(BytesIO(image_bytes))
                             if image.mode != "RGB":
                                 image = image.convert("RGB")
                             timestamp = int(time.time())
                             filename = f"image_{timestamp}_{i}.jpg"
                             filepath = os.path.join(self.output_dir, filename)
                             
                             exif = image.getexif()
                             # 0x010e: ImageDescription (Title)
                             exif[0x010e] = image_prompt
                             # 0x9286: UserComment (Comments)
                             exif[0x9286] = metadata_description
                             # 0x9c9b: XPTitle
                             exif[0x9c9b] = image_prompt.encode("utf-16le")
                             # 0x9c9c: XPComment
                             exif[0x9c9c] = metadata_description.encode("utf-16le")
                             
                             image.save(filepath, "JPEG", exif=exif, quality=95)
                             saved_paths.append(filepath)
                             print(f"[create_image tool] Saved image to {filepath}")
                
                if not saved_paths:
                    return "Failed to generate image: Model didn't return binary image data."

                self.last_create_time = time.time()
                return f"Successfully generated and saved image to {saved_paths[0]}"
            except Exception as e:
                error_msg = f"Error generating image: {e}"
                print(f"[create_image tool] {error_msg}")
                return error_msg

        def show_image(file_path: str) -> str:
            """Shows an image from a file path to the user.

            Args:
                file_path: The file path of the image to show.

            Returns:
                A status message indicating success or failure.
            """
            try:
                now = time.time()
                elapsed = now - self.last_show_time
                if elapsed < self.cooldown_duration:
                    remaining = int(self.cooldown_duration - elapsed)
                    return f"Error: show_image is on cooldown. Please wait {remaining} more seconds before displaying another image."

                print(f"[load_image tool] Showing image from {file_path}")
                if os.path.exists(file_path):
                    if self.on_show_image:
                        self.on_show_image(file_path)
                    self.last_show_time = time.time()
                    return f"Successfully displayed {file_path} to the user."
                else:
                    return f"Error: Image {file_path} not found."
            except Exception as e:
                print(f"[load_image tool] Error showing image: {e}")
                return f"Error showing image: {e}"

        def browse_images() -> list[str]:
            """Browse all available generated images.

            Returns:
                A list of file paths to all available images.
            """
            try:
                images = []
                for filename in os.listdir(self.output_dir):
                    if filename.lower().endswith((".png", ".jpg", ".jpeg")):
                        images.append(os.path.join(self.output_dir, filename))
                return images
            except Exception as e:
                return [f"Error browsing images: {e}"]

        def search_image_by_metadata(metadata_query: str) -> list[str]:
            """Search for images that match a given metadata description.

            Args:
                metadata_query: A string to search for in the image metadata description.

            Returns:
                A list of file paths to images whose metadata description contains the query.
            """
            try:
                matches = []
                for filename in os.listdir(self.output_dir):
                    if filename.lower().endswith((".png", ".jpg", ".jpeg")):
                        filepath = os.path.join(self.output_dir, filename)
                        image = Image.open(filepath)
                        metadata_desc = ""
                        
                        # Try PNG tEXt chunks first
                        if hasattr(image, "text") and image.text:
                            metadata_desc = image.text.get("MetadataDescription", "")
                        
                        # Fallback to EXIF metadata (JPEG or PNG EXIF)
                        if not metadata_desc:
                            try:
                                exif = image.getexif()
                                if exif:
                                    if 0x9286 in exif: # UserComment
                                        metadata_desc = exif[0x9286]
                                    elif 0x9c9c in exif: # XPComment
                                        val = exif[0x9c9c]
                                        if isinstance(val, bytes):
                                            metadata_desc = val.decode("utf-16le").rstrip("\x00")
                                        else:
                                            metadata_desc = str(val)
                            except Exception:
                                pass
                        
                        if metadata_desc and metadata_query.lower() in metadata_desc.lower():
                            matches.append(filepath)
                return matches
            except Exception as e:
                return [f"Error searching images: {e}"]


        self.create_image = create_image
        self.show_image = show_image
        self.browse_images = browse_images
        self.search_image_by_metadata = search_image_by_metadata
