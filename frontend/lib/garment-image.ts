type GarmentImage = {
  image_url?: string | null;
  medium_url?: string | null;
  thumbnail_url?: string | null;
};

export function garmentImageUrl(
  image: GarmentImage | null | undefined,
  size: 'small' | 'card' | 'detail',
): string | null {
  if (!image) return null;
  if (size === 'small') {
    return image.thumbnail_url || image.medium_url || image.image_url || null;
  }
  if (size === 'card') {
    return image.medium_url || image.image_url || image.thumbnail_url || null;
  }
  return image.image_url || image.medium_url || image.thumbnail_url || null;
}
