'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';

const MAX_NOTE_LENGTH = 1000;

interface RejectNoteDialogProps {
  open: boolean;
  onCancel: () => void;
  onConfirm: (note: string) => void;
}

export function RejectNoteDialog({ open, onCancel, onConfirm }: RejectNoteDialogProps) {
  const t = useTranslations('suggest.rejectNote');
  const tCommon = useTranslations('common');
  const [note, setNote] = useState('');

  const close = () => {
    setNote('');
    onCancel();
  };

  const confirm = () => {
    onConfirm(note.trim());
    setNote('');
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && close()}>
      <DialogContent className="sm:max-w-md" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{t('title')}</DialogTitle>
        </DialogHeader>
        <Textarea
          autoFocus
          aria-label={t('title')}
          value={note}
          maxLength={MAX_NOTE_LENGTH}
          placeholder={t('placeholder')}
          onChange={(event) => setNote(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) confirm();
          }}
          rows={3}
        />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={close}>
            {tCommon('cancel')}
          </Button>
          <Button variant="destructive" onClick={confirm}>
            {t('confirm')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
