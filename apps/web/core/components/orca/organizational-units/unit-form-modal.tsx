/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
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

export const OrganizationalUnitFormModal = observer(function OrganizationalUnitFormModal(props: Props) {
  const { isOpen, workspaceSlug, unit, onClose } = props;
  const store = useOrganizationalUnit();

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
        setToast({ type: TOAST_TYPE.SUCCESS, title: "Saved", message: `${name.trim()} was updated.` });
      } else {
        await store.createUnit(workspaceSlug, { name: name.trim(), description: description.trim() });
        setToast({ type: TOAST_TYPE.SUCCESS, title: "Created", message: `${name.trim()} is ready to use.` });
      }
      onClose();
    } catch (error) {
      const conflict = (error as { status?: number })?.status === 409;
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Not saved",
        message: conflict ? "An area with this name already exists. Pick a different name." : "Try again in a moment.",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-lg text-custom-text-100 font-medium">{unit ? "Edit area" : "New area"}</h3>

        <div className="flex flex-col gap-1">
          <label htmlFor="organizational-unit-name" className="text-sm text-custom-text-200">
            Name
          </label>
          <Input
            id="organizational-unit-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Compliance"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="organizational-unit-description" className="text-sm text-custom-text-200">
            Description
          </label>
          <TextArea
            id="organizational-unit-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="What this area is responsible for"
            rows={3}
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={handleSubmit} loading={isSubmitting} disabled={!name.trim()}>
            {unit ? "Save" : "Create area"}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
