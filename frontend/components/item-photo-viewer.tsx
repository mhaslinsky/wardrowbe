'use client';

import { ReactNode, useMemo, useRef, useState } from 'react';
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
  const common = useTranslations('common');
  const [currentIndex, setCurrentIndex] = useState(index);
  const [open, setOpen] = useState(false);
  const [portal, setPortal] = useState<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const zoom = useRef<ZoomRef>(null);
  const controller = useRef<ControllerRef>(null);
  const slides = useMemo(() => images.map((image) => ({ src: image.url, alt })), [images, alt]);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const dragged = useRef(false);
  const toggleZoom = () => {
    const current = zoom.current;
    if (current) current.changeZoom(current.zoom > 1 ? 1 : current.maxZoom);
  };
  const safeIndex = Math.max(0, Math.min(index, images.length - 1));

  return (
    <>
      <button ref={trigger} type="button" className="absolute inset-0 cursor-zoom-in"
        disabled={!images[safeIndex]?.url} aria-label={t('open')} onClick={() => {
          pointers.current.clear();
          dragged.current = false;
          setCurrentIndex(safeIndex);
          setOpen(true);
        }}>
        {children}
      </button>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Content ref={setPortal} aria-describedby={undefined}
            className="fixed inset-0 z-[60] outline-none"
            onPointerDownCapture={(event) => {
              if (pointers.current.size === 0) dragged.current = false;
              pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
              if (pointers.current.size > 1) dragged.current = true;
            }}
            onPointerMoveCapture={(event) => {
              const start = pointers.current.get(event.pointerId);
              if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) dragged.current = true;
            }}
            onPointerUpCapture={(event) => pointers.current.delete(event.pointerId)}
            onPointerCancelCapture={(event) => pointers.current.delete(event.pointerId)}
            onClickCapture={(event) => {
              // Browsers emit a click after a pan, which must not reset the zoom.
              if (dragged.current && event.detail > 0) {
                event.preventDefault();
                event.stopPropagation();
              }
            }}
            onEscapeKeyDown={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
            }}
            onKeyDownCapture={(event) => {
              if (event.key.toLowerCase() === 'z') {
                event.preventDefault();
                event.stopPropagation();
                toggleZoom();
              } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
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
                index={Math.max(0, Math.min(currentIndex, slides.length - 1))}
                slides={slides}
                portal={{ root: portal }}
                plugins={[Zoom]}
                zoom={{ ref: zoom, maxZoomPixelRatio: 1, doubleClickMaxStops: 1 }}
                controller={{ ref: controller, closeOnBackdropClick: true }}
                carousel={{ preload: 0 }}
                toolbar={{ buttons: ['close'] }}
                render={{ buttonZoom: ({ zoom: level, disabled }) => (
                  <button type="button" className="yarl__button" disabled={disabled}
                    aria-label={level > 1 ? t('fit') : t('actualSize')}
                    aria-pressed={level > 1} aria-keyshortcuts="Z" onClick={toggleZoom}>
                    {level > 1 ? t('fit') : t('actualSize')}
                  </button>
                ) }}
                labels={{
                  Close: common('close'), Previous: common('previous'), Next: common('next'),
                  Lightbox: t('lightbox'), 'Photo gallery': t('gallery'),
                  Carousel: t('carousel'), Slide: t('slide'),
                  '{index} of {total}': t('slideCount', { index: '{index}', total: '{total}' }),
                }}
                on={{
                  click: toggleZoom,
                  view: ({ index: viewedIndex }) => setCurrentIndex(viewedIndex),
                }}
              />
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
