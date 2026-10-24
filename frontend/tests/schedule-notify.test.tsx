import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import NotificationsPage from '@/app/dashboard/notifications/page'

const state = vi.hoisted(() => ({
  create: vi.fn().mockResolvedValue({}),
  update: vi.fn().mockResolvedValue({}),
}))
vi.mock('@/lib/hooks/use-notifications', () => ({
  useNotificationSettings: () => ({ data: [], isLoading: false }),
  useSchedules: () => ({ data: [{ id: 'weekend', day_of_week: 5, notification_time: '20:00', occasion: 'casual', enabled: true, notify_day_before: true, notify: false }], isLoading: false }),
  useCreateSchedule: () => ({ mutateAsync: state.create }),
  useUpdateSchedule: () => ({ mutateAsync: state.update }),
  useDeleteSchedule: () => ({ mutateAsync: vi.fn() }),
  useCreateNotificationSetting: () => ({ mutateAsync: vi.fn() }),
  useUpdateNotificationSetting: () => ({ mutateAsync: vi.fn() }),
  useDeleteNotificationSetting: () => ({ mutateAsync: vi.fn() }),
  useTestNotificationSetting: () => ({ mutateAsync: vi.fn() }),
  useTestNotificationChannel: () => ({ mutateAsync: vi.fn() }),
  useNtfyDefaults: () => ({ data: undefined }),
}))
vi.mock('@/lib/hooks/use-translated-constants', () => ({
  useOccasionLabel: () => (value: string) => value,
  useOccasions: () => [{ value: 'casual', label: 'Casual' }],
}))
vi.mock('@/lib/hooks/use-user', () => ({ useUserProfile: () => ({ data: undefined }) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => vi.clearAllMocks())

it('saves the notify toggle on an existing silent schedule', async () => {
  render(<NotificationsPage />)
  const toggle = screen.getByRole('switch', { name: 'schedule.sendNotification' })
  expect(toggle).toHaveAttribute('aria-checked', 'false')
  fireEvent.click(toggle)
  await waitFor(() => expect(state.update).toHaveBeenCalledWith({ id: 'weekend', data: { notify: true } }))
})

it('creates a silent schedule and resets notify after saving', async () => {
  render(<NotificationsPage />)
  fireEvent.click(screen.getByRole('button', { name: 'schedule.addSchedule' }))
  const toggles = screen.getAllByRole('switch', { name: 'schedule.sendNotification' })
  const formToggle = toggles[toggles.length - 1]
  expect(formToggle).toHaveAttribute('aria-checked', 'true')
  fireEvent.click(formToggle)
  const buttons = screen.getAllByRole('button', { name: 'schedule.addSchedule' })
  fireEvent.click(buttons[buttons.length - 1])
  await waitFor(() => expect(state.create).toHaveBeenCalledWith(expect.objectContaining({ notify: false })))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'schedule.addSchedule' }))
  const reset = screen.getAllByRole('switch', { name: 'schedule.sendNotification' })
  expect(reset[reset.length - 1]).toHaveAttribute('aria-checked', 'true')
})
