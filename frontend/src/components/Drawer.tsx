import React, {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
} from 'react';
import { BaseProps } from '../@types/common';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import useDrawer from '../hooks/useDrawer';
import ButtonIcon from './ButtonIcon';
import {
  PiChartLine,
  PiChatCenteredDotsDuotone,
  PiListBullets,
  PiPlugs,
  PiPresentationChart,
  PiShareNetwork,
  PiUserCheck,
  PiX,
} from 'react-icons/pi';
import { ConversationMeta } from '../@types/conversation';
import { BotListItem } from '../@types/bot';
import { isMobile } from 'react-device-detect';
import useChat from '../hooks/useChat';
import { useTranslation } from 'react-i18next';
import Menu from './Menu';
import RateLimitStatus from './RateLimitStatus';
import DrawerItem from './DrawerItem';
import ExpandableDrawerGroup from './ExpandableDrawerGroup';
import { usePageLabel } from '../routes';
import { twMerge } from 'tailwind-merge';
import IconPinnedBot from './IconPinnedBot';
import useGlobalConfig from '../hooks/useGlobalConfig';

type Props = BaseProps & {
  isAdmin: boolean;
  isAllowCreatingBot: boolean;
  conversations?: ConversationMeta[];
  pinnedBots?: BotListItem[];
  starredBots?: BotListItem[];
  recentlyUsedUnstarredBots?: BotListItem[];
  updateConversationTitle: (
    conversationId: string,
    title: string
  ) => Promise<void>;
  onSignOut: () => void;
  onDeleteConversation: (conversation: ConversationMeta) => void;
  onClearConversations: () => void;
  onSelectLanguage: () => void;
  onClickDrawerOptions: () => void;
};

const Drawer: React.FC<Props> = (props) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { getPageLabel } = usePageLabel();
  const { opened, switchOpen, drawerOptions } = useDrawer();
  const { pinnedBots } = props;

  const location = useLocation();

  const { newChat, conversationId } = useChat();
  const { botId } = useParams();
  const { getGlobalConfig } = useGlobalConfig();
  const { data: globalConfig } = getGlobalConfig();
  const logoSrc = globalConfig?.logoPath ?? '';

  const onClickNewBotChat = useCallback(
    () => {
      newChat();
      closeSmallDrawer();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const smallDrawer = useRef<HTMLDivElement>(null);

  const closeSmallDrawer = useCallback(() => {
    if (smallDrawer.current?.classList.contains('visible')) {
      switchOpen();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onClickLogo = useCallback(() => {
    navigate('/');
    closeSmallDrawer();
  }, [navigate, closeSmallDrawer]);

  useLayoutEffect(() => {
    // リサイズイベントを拾って状態を更新する
    const onResize = () => {
      if (isMobile) {
        return;
      }

      // 狭い画面のDrawerが表示されていて、画面サイズが大きくなったら状態を更新
      if (!smallDrawer.current?.checkVisibility() && opened) {
        switchOpen();
      }
    };
    onResize();

    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const isAdminPanel = useMemo(() => {
    return location.pathname.startsWith('/admin');
  }, [location.pathname]);

  return (
    <>
      <div className="relative h-full overflow-y-auto bg-aws-squid-ink-light scrollbar-thin scrollbar-track-white scrollbar-thumb-aws-squid-ink-light/30 dark:bg-aws-ui-color-dark dark:scrollbar-thumb-aws-ui-color-dark/30">
        <nav
          className={`lg:visible lg:w-64 ${
            opened ? 'visible w-64' : 'invisible w-0'
          } text-sm  text-white transition-width`}>
          {logoSrc && (
            <div className="sticky top-0 z-10 flex items-center justify-center border-b border-white/10 bg-aws-squid-ink-light px-4 py-6 dark:bg-aws-squid-ink-dark">
              <button
                type="button"
                onClick={onClickLogo}
                className="flex w-full items-center justify-center focus:outline-none focus:ring-2 focus:ring-white/60 focus:ring-offset-2 focus:ring-offset-transparent">
                <img
                  src={logoSrc}
                  alt={t('app.name')}
                  className="h-10 w-auto max-w-[170px]"
                  loading="lazy"
                />
              </button>
            </div>
          )}
          {!isAdminPanel && (
            <div className={props.isAdmin ? 'mb-20' : 'mb-10'}>
              {drawerOptions.show.myBots && props.isAllowCreatingBot && (
                <DrawerItem
                  isActive={false}
                  icon={<PiListBullets />}
                  to="/bot/my"
                  labelComponent={getPageLabel('/bot/my')}
                  onClick={closeSmallDrawer}
                />
              )}
              <DrawerItem
                isActive={false}
                icon={<PiShareNetwork />}
                to="/bot/shared"
                labelComponent={getPageLabel('/bot/shared')}
                onClick={closeSmallDrawer}
              />

              {drawerOptions.show.pinnedBots &&
                pinnedBots?.filter((bot) => bot.available).length ? (
                  <ExpandableDrawerGroup
                    label={t('app.pinnedBots')}
                    className="border-t bg-aws-squid-ink-light pt-1 dark:bg-aws-squid-ink-dark">
                    {pinnedBots
                      .filter((bot) => bot.available)
                      .map((bot) => (
                        <DrawerItem
                          key={bot.id}
                          isActive={botId === bot.id && !conversationId}
                          to={`/bot/${bot.id}`}
                          icon={<IconPinnedBot showAlways />}
                          labelComponent={bot.title}
                          onClick={onClickNewBotChat}
                        />
                      ))}
                  </ExpandableDrawerGroup>
                ) : null}
            </div>
          )}

          {isAdminPanel && (
            <>
              <div className="px-2 py-1 italic">{t('app.adminConsoles')}</div>
              <DrawerItem
                className="w-60"
                isActive={location.pathname === '/admin/shared-bot-analytics'}
                icon={<PiChartLine />}
                to="/admin/shared-bot-analytics"
                labelComponent={getPageLabel('/admin/shared-bot-analytics')}
                onClick={closeSmallDrawer}
              />
              <DrawerItem
                className="w-60"
                isActive={location.pathname === '/admin/api-management'}
                icon={<PiPlugs />}
                to="/admin/api-management"
                labelComponent={getPageLabel('/admin/api-management')}
                onClick={closeSmallDrawer}
              />
              <DrawerItem
                className="w-60"
                isActive={location.pathname === '/admin/user-approval'}
                icon={<PiUserCheck />}
                to="/admin/user-approval"
                labelComponent={getPageLabel('/admin/user-approval')}
                onClick={closeSmallDrawer}
              />
            </>
          )}

          <div
            className={twMerge(
              opened ? 'w-64' : 'w-0',
              props.isAdmin ? 'min-h-20' : 'min-h-10',
              'fixed -bottom-2 z-50 mb-2 flex flex-col items-start border-t bg-aws-squid-ink-light transition-width dark:bg-aws-ui-color-dark lg:w-64'
            )}>
            {props.isAdmin && !isAdminPanel && (
              <DrawerItem
                className="w-60"
                isActive={false}
                icon={<PiPresentationChart />}
                to="/admin/shared-bot-analytics"
                labelComponent={t('app.adminConsoles')}
                onClick={closeSmallDrawer}
              />
            )}
            {isAdminPanel && (
              <DrawerItem
                className="w-60"
                isActive={false}
                icon={<PiChatCenteredDotsDuotone />}
                to="/"
                labelComponent={t('app.backChat')}
                onClick={closeSmallDrawer}
              />
            )}
            <RateLimitStatus className="mx-2 w-60 py-1" />
            <Menu
              className="mx-2 flex h-10 w-60 justify-start"
              onSignOut={props.onSignOut}
              onSelectLanguage={props.onSelectLanguage}
              onClearConversations={props.onClearConversations}
              onClickDrawerOptions={props.onClickDrawerOptions}
            />
          </div>
        </nav>
      </div>

      <div
        ref={smallDrawer}
        className={`lg:hidden ${opened ? 'visible' : 'hidden'}`}>
        <ButtonIcon
          className="fixed left-64 top-0 z-50 text-white"
          onClick={switchOpen}>
          <PiX />
        </ButtonIcon>
        <div
          className="fixed z-40 h-dvh w-screen bg-dark-gray/90"
          onClick={switchOpen}></div>
      </div>
    </>
  );
};

export default Drawer;
