import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import ListPageLayout from '../layouts/ListPageLayout';
import Button from '../components/Button';
import InputText from '../components/InputText';
import Toggle from '../components/Toggle';
import useClaudeTeamsTokens from '../hooks/useClaudeTeamsTokens';
import { ClaudeTeamsToken } from '../@types/claude-teams';

// datetime-local <input> values are local time with no timezone info
// ("YYYY-MM-DDTHH:mm"); Date treats that as local time when constructed
// this way, matching how the input renders it back to the user.
const toEpochMs = (datetimeLocalValue: string): number | null => {
  if (!datetimeLocalValue) {
    return null;
  }
  const parsed = new Date(datetimeLocalValue);
  return Number.isNaN(parsed.getTime()) ? null : parsed.getTime();
};

const formatUtilization = (value: number | null): string => {
  if (value === null) {
    return '-';
  }
  return `${value.toFixed(1)}%`;
};

const formatResetsAt = (value: string | null): string => {
  if (!value) {
    return '-';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
};

const UsageBadge: React.FC<{
  token: ClaudeTeamsToken;
}> = ({ token }) => {
  const { t } = useTranslation();
  const usage = token.latestUsage;

  if (!usage) {
    return <span className="text-xs text-gray">{t('admin.claudeTeamsTokens.label.noUsageData')}</span>;
  }

  if (usage.isTokenExpired) {
    return (
      <span className="rounded bg-red px-2 py-0.5 text-xs font-bold text-aws-font-color-white-light">
        {t('admin.claudeTeamsTokens.label.tokenExpired')}
      </span>
    );
  }

  if (usage.fetchStatus === 'error') {
    const isScopeError = usage.fetchErrorMessage
      ?.toLowerCase()
      .includes('user:profile');
    return (
      <span className="text-xs text-aws-font-color-gray">
        {isScopeError
          ? t('admin.claudeTeamsTokens.label.fetchErrorScope')
          : t('admin.claudeTeamsTokens.label.fetchError')}
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 text-xs text-gray">
      <span>
        {t('admin.claudeTeamsTokens.label.fiveHourUsage')}:{' '}
        {formatUtilization(usage.fiveHourUtilization)}（
        {t('admin.claudeTeamsTokens.label.resetsAt')}:{' '}
        {formatResetsAt(usage.fiveHourResetsAt)}）
      </span>
      <span>
        {t('admin.claudeTeamsTokens.label.sevenDayUsage')}:{' '}
        {formatUtilization(usage.sevenDayUtilization)}（
        {t('admin.claudeTeamsTokens.label.resetsAt')}:{' '}
        {formatResetsAt(usage.sevenDayResetsAt)}）
      </span>
    </div>
  );
};

const AdminClaudeTeamsTokensPage: React.FC = () => {
  const { t } = useTranslation();
  const {
    tokens,
    isLoading,
    createToken,
    updateToken,
    deleteToken,
    regenerateIngestSecret,
    getRegistrationSecret,
    regenerateRegistrationSecret,
    downloadUsageHistoryCsv,
  } = useClaudeTeamsTokens();
  const [displayName, setDisplayName] = useState('');
  const [tokenValue, setTokenValue] = useState('');
  const [csvFrom, setCsvFrom] = useState('');
  const [csvTo, setCsvTo] = useState('');
  // Shown once right after registration or a secret regeneration -- never
  // fetched back from the API afterward, so this is the only place the
  // admin can copy it from.
  const [revealedIngestSecret, setRevealedIngestSecret] = useState<{
    tokenId: string;
    secret: string;
  } | null>(null);
  // Unlike per-token ingest secrets, this one is safe to re-fetch (only
  // an admin can reach this Cognito-authenticated route), so it's kept
  // in state and shown/hidden rather than gated behind a one-time reveal.
  const [registrationSecret, setRegistrationSecret] = useState<string | null>(
    null
  );

  const onShowRegistrationSecret = async () => {
    setRegistrationSecret(await getRegistrationSecret());
  };

  const onRegenerateRegistrationSecret = async () => {
    setRegistrationSecret(await regenerateRegistrationSecret());
  };

  const onSubmit = async () => {
    if (!displayName || !tokenValue) {
      return;
    }
    const created = await createToken({ displayName, tokenValue });
    setDisplayName('');
    setTokenValue('');
    setRevealedIngestSecret({
      tokenId: created.tokenId,
      secret: created.ingestSecret,
    });
  };

  const onRegenerateIngestSecret = async (tokenId: string) => {
    const secret = await regenerateIngestSecret(tokenId);
    setRevealedIngestSecret({ tokenId, secret });
  };

  const onDownloadCsv = async () => {
    const start = toEpochMs(csvFrom);
    const end = toEpochMs(csvTo);
    if (start === null || end === null) {
      return;
    }
    await downloadUsageHistoryCsv({ start, end });
  };

  return (
    <ListPageLayout
      pageTitle={t('admin.claudeTeamsTokens.label.pageTitle')}
      isLoading={isLoading}
      isEmpty={tokens?.length === 0}
      emptyMessage={t('admin.claudeTeamsTokens.label.noTokens')}
      searchCondition={
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2 rounded border p-4">
            <div className="text-sm font-bold">
              {t('admin.claudeTeamsTokens.label.registrationSecretTitle')}
            </div>
            <div className="text-xs">
              {t('admin.claudeTeamsTokens.label.registrationSecretHint')}
            </div>
            {registrationSecret && (
              <code className="select-all break-all rounded bg-aws-paper-light p-2 text-xs">
                {registrationSecret}
              </code>
            )}
            <div className="flex gap-2">
              <Button outlined onClick={onShowRegistrationSecret}>
                {t('admin.claudeTeamsTokens.button.showRegistrationSecret')}
              </Button>
              <Button outlined onClick={onRegenerateRegistrationSecret}>
                {t('admin.claudeTeamsTokens.button.regenerateRegistrationSecret')}
              </Button>
            </div>
          </div>
          <div className="flex flex-col gap-2 rounded border p-4">
            <div className="text-sm font-bold">
              {t('admin.claudeTeamsTokens.label.manualRegisterTitle')}
            </div>
            <InputText
              label={t('admin.claudeTeamsTokens.label.displayName')}
              value={displayName}
              onChange={setDisplayName}
            />
            <InputText
              label={t('admin.claudeTeamsTokens.label.tokenValue')}
              value={tokenValue}
              onChange={setTokenValue}
              type="password"
            />
            <Button onClick={onSubmit}>
              {t('admin.claudeTeamsTokens.button.register')}
            </Button>
          </div>
          {revealedIngestSecret && (
            <div className="flex flex-col gap-1 rounded border border-aws-font-color-blue-dark bg-light-blue p-4">
              <div className="text-sm font-bold">
                {t('admin.claudeTeamsTokens.label.ingestSecretRevealed')}
              </div>
              <div className="text-xs">
                {t('admin.claudeTeamsTokens.label.ingestSecretRevealedHint')}
              </div>
              <code className="select-all break-all rounded bg-white p-2 text-xs">
                {revealedIngestSecret.secret}
              </code>
              <Button
                outlined
                className="self-start"
                onClick={() => setRevealedIngestSecret(null)}>
                {t('admin.claudeTeamsTokens.button.dismiss')}
              </Button>
            </div>
          )}
          <div className="flex flex-col gap-2 rounded border p-4">
            <div className="text-sm font-bold">
              {t('admin.claudeTeamsTokens.label.csvDownload')}
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col text-xs">
                {t('admin.claudeTeamsTokens.label.csvFrom')}
                <input
                  type="datetime-local"
                  className="rounded border p-1"
                  value={csvFrom}
                  onChange={(e) => setCsvFrom(e.target.value)}
                />
              </label>
              <label className="flex flex-col text-xs">
                {t('admin.claudeTeamsTokens.label.csvTo')}
                <input
                  type="datetime-local"
                  className="rounded border p-1"
                  value={csvTo}
                  onChange={(e) => setCsvTo(e.target.value)}
                />
              </label>
              <Button
                outlined
                disabled={!csvFrom || !csvTo}
                onClick={onDownloadCsv}>
                {t('admin.claudeTeamsTokens.button.download')}
              </Button>
            </div>
          </div>
        </div>
      }>
      {tokens?.map((token) => (
        <div
          key={token.tokenId}
          className="flex items-center justify-between border-b p-2">
          <div>
            <div className="font-bold">{token.displayName}</div>
            <div className="text-xs">
              {token.isCoolingDown &&
                t('admin.claudeTeamsTokens.label.coolingDown')}
            </div>
            <UsageBadge token={token} />
          </div>
          <div className="flex items-center gap-2">
            <Toggle
              value={token.enabled}
              onChange={(checked) =>
                updateToken(token.tokenId, { enabled: checked })
              }
            />
            <Button
              outlined
              onClick={() => onRegenerateIngestSecret(token.tokenId)}>
              {t('admin.claudeTeamsTokens.button.regenerateIngestSecret')}
            </Button>
            <Button
              outlined
              onClick={() => deleteToken(token.tokenId)}>
              {t('admin.claudeTeamsTokens.button.delete')}
            </Button>
          </div>
        </div>
      ))}
    </ListPageLayout>
  );
};

export default AdminClaudeTeamsTokensPage;
