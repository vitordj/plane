/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IOrganizationalUnit } from "@plane/types";
import { EModalPosition, EModalWidth, Input, ModalCore, TextArea } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  /** Editing an existing area when present; creating a new one when omitted. */
  unit?: IOrganizationalUnit;
  onClose: () => void;
};

const OU = "workspace_settings.settings.organizational_units";

export const OrganizationalUnitFormModal = observer(function OrganizationalUnitFormModal(props: Props) {
  const { isOpen, workspaceSlug, unit, onClose } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setName(unit?.name ?? "");
    setDescription(unit?.description ?? "");
  }, [isOpen, unit]);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setIsSubmitting(true);
    try {
      if (unit) {
        await store.updateUnit(workspaceSlug, unit.id, { name: name.trim(), description: description.trim() });
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${OU}.toast.saved`),
          message: t(`${OU}.form.toast.updated`, { name: name.trim() }),
        });
      } else {
        await store.createUnit(workspaceSlug, { name: name.trim(), description: description.trim() });
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${OU}.form.toast.created_title`),
          message: t(`${OU}.form.toast.created`, { name: name.trim() }),
        });
      }
      onClose();
    } catch (error) {
      const conflict = (error as { status?: number })?.status === 409;
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.toast.not_saved`),
        message: conflict ? t(`${OU}.form.toast.name_taken`) : t(`${OU}.try_again`),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-lg text-custom-text-100 font-medium">
          {unit ? t(`${OU}.form.edit_title`) : t(`${OU}.add`)}
        </h3>

        <div className="flex flex-col gap-1">
          <label htmlFor="organizational-unit-name" className="text-sm text-custom-text-200">
            {t("common.name")}
          </label>
          <Input
            id="organizational-unit-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t(`${OU}.form.name_placeholder`)}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="organizational-unit-description" className="text-sm text-custom-text-200">
            {t("common.description")}
          </label>
          <TextArea
            id="organizational-unit-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder={t(`${OU}.form.description_placeholder`)}
            rows={3}
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="sm" onClick={handleSubmit} loading={isSubmitting} disabled={!name.trim()}>
            {unit ? t("save") : t(`${OU}.form.create`)}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
