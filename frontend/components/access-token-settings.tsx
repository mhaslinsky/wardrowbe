'use client';

import { useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useFormatter } from 'next-intl';
import { z } from 'zod';
import { api } from '@/lib/api';
import { applySessionToken, useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const tokenInfo = z.object({
  id: z.string().uuid(), name: z.string(),
  created_at: z.string().datetime({ offset: true }),
  last_used_at: z.string().datetime({ offset: true }).nullable(),
});
const endpoint = '/users/me/access-tokens';

export function AccessTokenSettings() {
  const translate = useTranslations('settings.accessTokens');
  const format = useFormatter();
  const { data: session, status } = useSession();
  useSetTokenIfAvailable();
  const client = useQueryClient();
  const key = ['access-tokens', session?.user?.email];
  const [name, setName] = useState('');
  const [revealed, setRevealed] = useState<{ owner: string; id: string; token: string } | null>(null);
  useEffect(() => { setRevealed(null); }, [session?.user?.email, status]);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const query = useQuery({
    queryKey: key,
    queryFn: async () => tokenInfo.array().parse(await api.get<unknown>(endpoint)),
    enabled: status === 'authenticated',
    retry: false,
    meta: { toastsOwnErrors: true },
  });
  const mutation = useMutation({
    gcTime: 0,
    meta: { toastsOwnErrors: true },
    mutationFn: async (action: { name: string } | { id: string }) => {
      applySessionToken(session);
      if ('name' in action) {
        setRevealed(null); setCopied(false); setCopyFailed(false);
        const created = tokenInfo.extend({ token: z.string().startsWith('wbt_') }).parse(await api.post<unknown>(endpoint, action));
        setRevealed({ owner: session?.user?.email ?? '', id: created.id, token: created.token });
        setName('');
      } else {
        await api.delete(`${endpoint}/${action.id}`);
        if (revealed?.id === action.id) setRevealed(null);
      }
    },
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const secret = status === 'authenticated' && revealed?.owner === session?.user?.email ? revealed : null;
  const date = (value: string) => format.dateTime(new Date(value), { dateStyle: 'medium', timeStyle: 'short' });
  return <Card>
    <CardHeader><CardTitle>{translate('title')}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
        event.preventDefault();
        if (name.trim()) mutation.mutate({ name: name.trim() });
      }}>
        <label><span className="block text-sm">{translate('name')}</span>
          <Input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <Button disabled={mutation.isPending || !name.trim()}>{translate('create')}</Button>
      </form>
      {secret && <div className="space-y-2 rounded border p-3">
        <p>{translate('warning')}</p>
        <Input readOnly aria-label={translate('token')} value={secret.token} autoComplete="off" />
        <Button variant="outline" onClick={async () => {
          try { await navigator.clipboard.writeText(secret.token); setCopied(true); setCopyFailed(false); }
          catch { setCopyFailed(true); }
        }}>{translate(copied ? 'copied' : 'copy')}</Button>
        <Button variant="ghost" onClick={() => setRevealed(null)}>{translate('dismiss')}</Button>
      </div>}
      {(query.isError || mutation.isError || copyFailed) && <p role="alert">{translate('failed')}</p>}
      {query.isPending && <p role="status">{translate('loading')}</p>}
      {query.data?.length === 0 && <p>{translate('empty')}</p>}
      {query.data && query.data.length > 0 && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <thead><tr>{(['name', 'created', 'lastUsed', 'actions'] as const).map((field) => <th className="p-2" key={field}>{translate(field)}</th>)}</tr></thead>
        <tbody>{query.data.map((token) => <tr key={token.id}>
          <td className="p-2">{token.name}</td><td className="p-2">{date(token.created_at)}</td>
          <td className="p-2">{token.last_used_at ? date(token.last_used_at) : translate('never')}</td>
          <td className="p-2"><Button variant="outline" disabled={mutation.isPending} onClick={() => mutation.mutate({ id: token.id })}>{translate('revoke')}</Button></td>
        </tr>)}</tbody>
      </table></div>}
    </CardContent>
  </Card>;
}
