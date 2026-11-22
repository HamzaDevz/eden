// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Create a compute pipeline from a SPIR-V file on the device driver: vkcomp file.spv BINDINGS
// BINDINGS: one letter per binding (u=ubo b=ssbo t=uniform texel s=storage texel i=sampled c=combined)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

static VkDescriptorType type_of(char c) {
    switch (c) {
    case 'u': return VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
    case 'b': return VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    case 't': return VK_DESCRIPTOR_TYPE_UNIFORM_TEXEL_BUFFER;
    case 's': return VK_DESCRIPTOR_TYPE_STORAGE_TEXEL_BUFFER;
    case 'i': return VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE;
    case 'c': return VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    default: return VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
    }
}

int main(int argc, char** argv) {
    if (argc < 3) return 2;
    FILE* f = fopen(argv[1], "rb");
    if (!f) return 3;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint32_t* code = malloc(size);
    fread(code, 1, size, f);
    fclose(f);

    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, 0, "vkcomp", 1, "vkcomp", 1, VK_API_VERSION_1_3};
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, 0, 0, &app};
    VkInstance inst;
    if (vkCreateInstance(&ici, 0, &inst)) return 4;
    uint32_t n = 1;
    VkPhysicalDevice pd;
    vkEnumeratePhysicalDevices(inst, &n, &pd);

    VkPhysicalDeviceVulkan13Features f13 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
    VkPhysicalDeviceVulkan12Features f12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, &f13};
    VkPhysicalDeviceVulkan11Features f11 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, &f12};
    VkPhysicalDeviceFeatures2 f2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &f11};
    vkGetPhysicalDeviceFeatures2(pd, &f2);
    f2.features.robustBufferAccess = VK_FALSE;

    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, 0, 0, 0, 1, &prio};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &f2, 0, 1, &qci};
    VkDevice dev;
    VkResult r = vkCreateDevice(pd, &dci, 0, &dev);
    if (r) { printf("device %d\n", r); return 5; }

    const char* b = argv[2];
    int nb = strlen(b);
    VkDescriptorSetLayoutBinding binds[64];
    for (int i = 0; i < nb; ++i) {
        binds[i] = (VkDescriptorSetLayoutBinding){i, type_of(b[i]), 1, VK_SHADER_STAGE_COMPUTE_BIT, 0};
    }
    VkDescriptorSetLayoutCreateInfo dl = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, 0, 0, nb, binds};
    VkDescriptorSetLayout set_layout;
    vkCreateDescriptorSetLayout(dev, &dl, 0, &set_layout);
    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 128};
    VkPipelineLayoutCreateInfo pl = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, 0, 0, 1, &set_layout, 1, &pcr};
    VkPipelineLayout layout;
    vkCreatePipelineLayout(dev, &pl, 0, &layout);

    VkShaderModuleCreateInfo smci = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, 0, 0, size, code};
    VkShaderModule mod;
    r = vkCreateShaderModule(dev, &smci, 0, &mod);
    if (r) { printf("module %d\n", r); return 6; }
    VkComputePipelineCreateInfo cci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    cci.stage = (VkPipelineShaderStageCreateInfo){VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, 0, 0,
                                                  VK_SHADER_STAGE_COMPUTE_BIT, mod, "main"};
    cci.layout = layout;
    VkPipeline pipe;
    r = vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cci, 0, &pipe);
    printf("%s %s\n", r == VK_SUCCESS ? "OK" : "FAIL", argv[1]);
    return r == VK_SUCCESS ? 0 : 1;
}
