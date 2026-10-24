'use client';

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { useSubmitFeedback, type FeedbackSummary, type Outfit } from '@/lib/hooks/use-outfits';
import { useTranslations } from 'next-intl';
import { StarRatingInput } from '@/components/shared/star-rating';

export type FeedbackTarget = Pick<Outfit, 'id' | 'items'> & {
  feedback: (Pick<FeedbackSummary, 'rating' | 'comment'> & Partial<Pick<FeedbackSummary, 'worn_at'>>) | null;
};

interface FeedbackDialogProps {
  noteOnly?: boolean;
  outfit: FeedbackTarget;
  open: boolean;
  onClose: () => void;
}

export function FeedbackDialog({ outfit, open, onClose, noteOnly = false }: FeedbackDialogProps) {
  const t = useTranslations('suggest.feedback');
  const tc = useTranslations('common');
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const submitFeedback = useSubmitFeedback();
  const canRate = !noteOnly && !!outfit.feedback?.worn_at;

  useEffect(() => {
    if (open) {
      setRating(outfit.feedback?.rating ?? 0);
      setComment(outfit.feedback?.comment ?? '');
    }
  }, [open, outfit.id, outfit.feedback]);

  const handleSubmit = async () => {
    try {
      await submitFeedback.mutateAsync({
        outfitId: outfit.id,
        feedback: canRate ? { rating: rating > 0 ? rating : undefined, comment: comment.trim() } : { comment: comment.trim() },
      });
      toast.success(t('submitted'));
      onClose();
    } catch { toast.error(t('submitError')); }
  };

  return <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
    <DialogContent className="max-w-md">
      <DialogHeader>
        <DialogTitle>{canRate ? t('rateThisOutfit') : t('addNote')}</DialogTitle>
        <DialogDescription>{canRate ? t('howDidItWork') : t('noteHint')}</DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-4">
        {canRate && <div><label className="text-sm font-medium mb-2 block">{t('overallRating')}</label><StarRatingInput value={rating} onChange={setRating} /></div>}
        <Textarea aria-label={t('commentsOptional')} placeholder={t('commentsPlaceholder')} value={comment} onChange={(event) => setComment(event.target.value)} />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>{tc('cancel')}</Button>
        <Button onClick={handleSubmit} disabled={submitFeedback.isPending}>{submitFeedback.isPending ? t('submitting') : tc('submit')}</Button>
      </div>
    </DialogContent>
  </Dialog>;
}
