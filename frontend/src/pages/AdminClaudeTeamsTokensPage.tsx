import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import ListPageLayout from '../layouts/ListPageLayout';
import Button from '../components/Button';
import InputText from '../components/InputText';
import Toggle from '../components/Toggle';
import useClaudeTeamsTokens from '../hooks/useClaudeTeamsTokens';

const AdminClaudeTeamsTokensPage: React.FC = () => {
  const { t } = useTranslation();
  const { tokens, isLoading, createToken, updateToken, deleteToken } =
    useClaudeTeamsTokens();
  const [displayName, setDisplayName] = useState('');
  const [tokenValue, setTokenValue] = useState('');

  const onSubmit = async () => {
    if (!displayName || !tokenValue) {
      return;
    }
    await createToken({ displayName, tokenValue });
    setDisplayName('');
    setTokenValue('');
  };

  return (
    <ListPageLayout
      pageTitle={t('admin.claudeTeamsTokens.label.pageTitle')}
      isLoading={isLoading}
      isEmpty={tokens?.length === 0}
      emptyMessage={t('admin.claudeTeamsTokens.label.noTokens')}>
      <div className="mb-4 flex flex-col gap-2 rounded border p-4">
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
