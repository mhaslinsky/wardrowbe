import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
const bridge = vi.fn();
const push = vi.fn();
const invalidateQueries = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries }) }));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }));
const user = { id: 'primary-user', email: 'primary@example.test' };
vi.mock('@/lib/hooks/use-user', () => ({ useUserProfile: () => ({ data: user }) }));
vi.mock('@/lib/hooks/use-considering-import', () => ({ useConsideringImport: () => bridge }));
import { ConsideringImport } from '@/components/considering-import';
const capture = { source_url: 'https://store.example/shirt', name: 'Shirt', images: ['https://store.example/front.jpg', 'https://store.example/back.jpg'], sizes: ['XL'], colors: ['blue'], selected_color: 'blue', material_detail: '80% cotton', selected_size: 'XL' };
beforeEach(() => {
  vi.clearAllMocks();
  bridge.mockImplementation(async (action: string) => action === 'load' ? { capture } : action === 'save' ? { saved: true, item: 'created-item' } : { cancelled: true });
});
describe('considering confirmation', () => {
  it('edits untrusted fields and selected images, then saves only on explicit submit', async () => {
    render(<ConsideringImport />);
    await screen.findByDisplayValue('Shirt');
    expect(bridge.mock.calls.map(call => call[0])).toEqual(['load']);
    fireEvent.change(screen.getByLabelText('material_detail'), { target: { value: '100% cotton' } });
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(screen.getByText('save'));
    await waitFor(() => expect(push).toHaveBeenCalledWith('/dashboard/considering'));
    expect(bridge).toHaveBeenCalledWith('save', expect.objectContaining({ material_detail: '100% cotton', images: [capture.images[0]] }));
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['considering', user.email] });
  });
  it('cancels before save without creating anything', async () => {
    render(<ConsideringImport />); await screen.findByDisplayValue('Shirt');
    fireEvent.click(screen.getByText('cancel'));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(bridge.mock.calls.map(call => call[0])).toEqual(['load', 'cancel']);
  });
  it('keeps partial saves recoverable and prevents cancellation or a second create', async () => {
    bridge.mockImplementation(async (action: string) => action === 'load' ? { capture, partial: true } : { saved: true, item: 'created-item' });
    render(<ConsideringImport />); await screen.findByText('partial');
    expect(screen.getByText('cancel')).toBeDisabled();
    expect(screen.getByLabelText('name')).toBeDisabled();
    fireEvent.click(screen.getByText('retry'));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(bridge.mock.calls.filter(call => call[0] === 'save')).toHaveLength(1);
  });
  it('does not save empty image selections or hide load errors', async () => {
    const view = render(<ConsideringImport />); await screen.findByDisplayValue('Shirt');
    for (const checkbox of screen.getAllByRole('checkbox')) fireEvent.click(checkbox);
    expect(screen.getByText('save')).toBeDisabled();
    view.unmount(); bridge.mockRejectedValue(Error('bridge'));
    render(<ConsideringImport />); await screen.findByRole('alert');
    expect(screen.getByRole('alert')).toHaveTextContent('loadFailed');
  });
});
