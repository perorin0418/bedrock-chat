import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import useBot from '../hooks/useBot';
import useChat from '../hooks/useChat';
import useLoginUser from '../hooks/useLoginUser';
import ListItemBot from '../components/ListItemBot';
import MenuBot from '../components/MenuBot';
import { copyBotUrl } from '../utils/BotUtils';
import ListPageLayout from '../layouts/ListPageLayout';

const BotSharedPage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isAdmin } = useLoginUser();
  const { newChat } = useChat();
  const { sharedBots, isLoadingSharedBots } = useBot();

  const onClickBot = useCallback(
    (botId: string) => {
      newChat();
      navigate(`/bot/${botId}`);
    },
    [navigate, newChat]
  );

  return (
    <ListPageLayout
      pageTitle={t('bot.shared.label.pageTitle')}
      isLoading={isLoadingSharedBots}
      isEmpty={sharedBots?.length === 0}
      emptyMessage={t('bot.label.noSharedBots')}>
      {sharedBots?.map((bot) => (
        <ListItemBot key={bot.id} bot={bot} onClick={onClickBot}>
          <div className="flex items-center">
            <MenuBot
              onClickCopyUrl={() => {
                copyBotUrl(bot.id);
              }}
              {...(isAdmin && {
                onClickBotManagement: () => {
                  navigate(`/admin/bot/${bot.id}`);
                },
              })}
            />
          </div>
        </ListItemBot>
      ))}
    </ListPageLayout>
  );
};

export default BotSharedPage;
