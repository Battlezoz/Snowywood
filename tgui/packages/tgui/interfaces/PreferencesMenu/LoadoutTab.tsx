import {
  Box,
  Button,
  LabeledList,
  Section,
  Stack,
} from 'tgui-core/components';

import { useBackend } from '../../backend';
// Searchable drop-in: stock Dropdown for short lists, adds a filter box once a
// list passes 7 options. (Replaces the per-tab RawDropdown + inline-Box wrapper.)
import { SearchableDropdown as Dropdown } from '../common/SearchableDropdown';

type LoadoutSlot = {
  slot: number;
  name: string;
  desc?: string;
  cost: number;
  hex?: string;
  color_name: string;
  custom_name?: string;
  custom_desc?: string;
  // Spritesheet CSS class for the item's icon (null when the slot is empty).
  icon?: string;
};

type LoadoutDynamicData = {
  slots: LoadoutSlot[];
  total_points: number;
  spent_points: number;
  remaining_points: number;
};

type LoadoutStaticData = {
  item_options: string[];
  color_options: string[];
};

type LoadoutData = LoadoutDynamicData & LoadoutStaticData;

type Data = {
  loadout: LoadoutDynamicData;
  loadout_static: LoadoutStaticData;
};

const SLOT_LABELS = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];

export const LoadoutTab = (props) => {
  const { act, data } = useBackend<Data>();
  // Merge static option lists (item_options, color_options) into the
  // dynamic loadout (slots). Defaults are applied post-spread so the brief
  // gap before the server's set_tab reply lands doesn't crash on
  // slots.map(...) — declaring them before the spread would trigger TS2783
  // duplicate-key errors when the typed spreads already declare the same
  // keys.
  const merged = { ...data.loadout_static, ...data.loadout };
  const loadout: LoadoutData = {
    slots: merged.slots ?? [],
    total_points: merged.total_points ?? 0,
    spent_points: merged.spent_points ?? 0,
    remaining_points: merged.remaining_points ?? 0,
    item_options: merged.item_options ?? [],
    color_options: merged.color_options ?? [],
  };

  const overbudget = loadout.remaining_points < 0;

  return (
    <Stack vertical>
      <Stack.Item>
        <Section title="Point Budget">
          <Box color="label" italic mb={1}>
            Base 10 points, +1 per selected vice. Spent on costed loadout items.
          </Box>
          <LabeledList>
            <LabeledList.Item label="Remaining">
              <Box inline bold color={overbudget ? 'bad' : 'good'}>
                {loadout.remaining_points}
              </Box>
            </LabeledList.Item>
            <LabeledList.Item label="Spent / Total">
              {loadout.spent_points} / {loadout.total_points}
            </LabeledList.Item>
          </LabeledList>
        </Section>
      </Stack.Item>
      <Stack.Item>
        <Section title="Loadout Items">
          <Box mb={1} color="label" italic>
            Loadout items are not given at spawn. RMB a tree, statue, or clock
            to collect them.
          </Box>
          <LabeledList>
            {loadout?.slots.map((s) => (
              <LabeledList.Item
                key={s.slot}
                label={`Item ${SLOT_LABELS[s.slot - 1]}`}
              >
                {/* Item icon preview (themed to match the new menu): the item's
                    sprite in a bordered swatch, or a dark placeholder when empty.
                    The spritesheet class carries the sprite's own dimensions. */}
                {s.name !== 'None' && s.icon ? (
                  <Box
                    inline
                    mr={1}
                    className={s.icon}
                    style={{
                      border: '1px solid #161616',
                      verticalAlign: 'middle',
                      imageRendering: 'pixelated',
                    }}
                  />
                ) : (
                  <Box
                    inline
                    mr={1}
                    width="32px"
                    height="32px"
                    style={{
                      border: '1px solid #161616',
                      backgroundColor: '#1b1b1b',
                      verticalAlign: 'middle',
                    }}
                  />
                )}
                <Dropdown
                  width="240px"
                  menuWidth="280px"
                  selected={s.name}
                  displayText={s.name}
                  options={loadout.item_options}
                  onSelected={(value) =>
                    value !== s.name &&
                    act('set_loadout_slot_direct', {
                      slot: s.slot,
                      name: value,
                    })
                  }
                />
                {s.cost > 0 && (
                  <Box inline ml={1} color="label">
                    ({s.cost} pt{s.cost === 1 ? '' : 's'})
                  </Box>
                )}
                {/* Native span carries the HTML title attribute (tgui's
                    Box doesn't whitelist it); Box keeps the swatch
                    styling. */}
                <span title={s.hex || '(no color set)'}>
                  <Box
                    inline
                    ml={1}
                    width="20px"
                    height="14px"
                    backgroundColor={s.hex || '#ffffff'}
                    style={{
                      border: '1px solid #161616',
                      verticalAlign: 'middle',
                    }}
                  />
                </span>
                <Box inline ml={1}>
                  <Dropdown
                    width="160px"
                    menuWidth="220px"
                    selected={s.color_name}
                    displayText={s.color_name}
                    options={loadout.color_options}
                    onSelected={(value) =>
                      value !== s.color_name &&
                      act('set_loadout_hex_direct', {
                        slot: s.slot,
                        name: value,
                      })
                    }
                  />
                </Box>
                {s.name !== 'None' && (
                  <Box inline ml={1}>
                    <Button
                      icon="pen"
                      tooltip={
                        s.custom_name
                          ? `Custom name: ${s.custom_name}`
                          : 'Set a custom name'
                      }
                      selected={!!s.custom_name}
                      onClick={() => act('set_loadout_name', { slot: s.slot })}
                    />
                    <Button
                      icon="align-left"
                      tooltip={
                        s.custom_desc
                          ? `Custom description set`
                          : 'Set a custom description'
                      }
                      selected={!!s.custom_desc}
                      onClick={() => act('set_loadout_desc', { slot: s.slot })}
                    />
                  </Box>
                )}
              </LabeledList.Item>
            ))}
          </LabeledList>
        </Section>
      </Stack.Item>
    </Stack>
  );
};
