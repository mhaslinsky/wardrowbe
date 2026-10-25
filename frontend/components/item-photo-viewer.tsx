'use client';

import { ReactNode, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import Lightbox, { ControllerRef } from 'yet-another-react-lightbox';
import Zoom, { ZoomRef } from 'yet-another-react-lightbox/plugins/zoom';
import { useTranslations } from 'next-intl';
import 'yet-another-react-lightbox/styles.css';

interface ItemPhotoViewerProps {
  images: { url: string; id: string }[];
  index: number;
  alt: string;
  children: ReactNode;
}

export function ItemPhotoViewer({ images, index, alt, children }: ItemPhotoViewerProps) {
  const t = useTranslations('wardrobe.photoViewer');
  const [open, setOpen] = useState(false);
  const [portal, setPortal] = useState<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const zoom = useRef<ZoomRef>(null);
  const controller = useRef<ControllerRef>(null);
  const safeIndex = Math.max(0, Math.min(index, images.length - 1));

  return (
    <>
      <button ref={trigger} type="button" className="absolute inset-0 cursor-zoom-in"
        disabled={!images[safeIndex]?.url} aria-label={t('open')} onClick={() => setOpen(true)}>
        {children}
      </button>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Content ref={setPortal} aria-describedby={undefined}
            className="fixed inset-0 z-[60] outline-none"
            onEscapeKeyDown={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
            }}
            onKeyDownCapture={(event) => {
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault();
                event.stopPropagation();
                if (event.key === 'ArrowLeft') controller.current?.prev();
                else controller.current?.next();
              }
            }}
            onCloseAutoFocus={(event) => { event.preventDefault(); trigger.current?.focus(); }}>
            <Dialog.Title className="sr-only">{alt}</Dialog.Title>
            {portal && (
              <Lightbox
                open={open} close={() => setOpen(false)}
                index={safeIndex}
                slides={images.map((image) => ({ src: image.url, alt }))}
                portal={{ root: portal }}
                plugins={[Zoom]}
                zoom={{ ref: zoom, maxZoomPixelRatio: 1, doubleClickMaxStops: 1 }}
                controller={{ ref: controller, closeOnBackdropClick: true }}
                carousel={{ preload: 0 }}
                toolbar={{ buttons: ['close'] }}
                render={{ buttonZoom: () => null }}
                labels={{ Close: t('close'), Previous: t('previous'), Next: t('next') }}
                on={{
                  click: () => {
                    const current = zoom.current;
                    if (current) current.changeZoom(current.zoom > 1 ? 1 : current.maxZoom);
                  },
                }}
              />
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
